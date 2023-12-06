from flask import Flask, request, abort
from git import Repo
import ansible_runner
import os
import requests
import json

app = Flask(__name__)

def updateRepo(): #Update the local git repo
    if os.path.isdir('ansible-switches'): #If the clone of the repo exists pull updates, if not clone it 
        repo = Repo('ansible-switches')
        o = repo.remotes.origin
        o.pull()
        print('pulled')
    else:
        Repo.clone_from("https://github.com/tssala23/ansible-switches.git", "ansible-switches")
        print('cloned')

def fileChanges(commits): #Returns bool for if vlan file changes and list of individual switch changes
    
    vlanChange = False #Vlan Flag
    switchChanges = [] #List of modified switches

    for commit in commits: #Look at each commit in push to main branch
        
        for file in commit['modified']: #Look at the files modified in each commit
            
            if file == 'group_vars/all/vlans.yaml' : #Set flag to true if vlan change
                vlanChange = True
                
            elif file[:9] == 'host_vars' : #Add all changed switches to a list 
                tmp = file.split("/") #Splits file path so switch name can be accessed
                switchChanges.append(tmp[1]) #Adds switch name to list

    print('Finshed filechange')
    print(vlanChange)
    print(switchChanges)

    return(vlanChange, switchChanges)

def runAnsible(vlanFlag, switchList): #Function for running ansible playbook
    if vlanFlag: #If the vlanFlag is true run playbook on all switches else only on switches with changes
        runAll = ansible_runner.run(
            private_data_dir='ansible-switches/',
            playbook='deploy.yaml'
         )
        print('Vlan flag all switches updates')
        return(runAll.status, runAll.stdout)

    else:
        strSwitchList = ','.join(map(str,switchList)) #Convert the list to string, items seperated by commas
        runSwitches = ansible_runner.run(
            private_data_dir='ansible-switches/',
            playbook='deploy.yaml',
            limit= strSwitchList #equivalent to --limit
        )
        print('Individual switches updated')
        return(runSwitches.status, runSwitches.stdout)
    
def sendAlert(vlanFlag, switchList, status, stdoutPath): #Function to send notification to slack channel

    webhook_url='https://hooks.slack.com/services/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'
    strSwitchList = ','.join(map(str,switchList)) #Convert the list to string, items seperated by commas

    if status == 'failed':
        color = '#f00216' #red color
        title = ("Switch Change :large_red_square:")
    else:
        color = '#05eb2f' #green color
        title = ("Switch Change :large_green_square:")

    if vlanFlag: #Create the messages for the alerts, different depending on what switches were changed
        message = 'Change made to switches.\nAll switches were changed.\nStatus:' + status + '\nPath to stdout: ' + str(stdoutPath.name)
    else:
        message = 'Change made to switches.\nSwitches: \n' + strSwitchList + '\nStatus: ' + status + '\nPath to stdout: ' + str(stdoutPath.name)

    slack_data = {
        "username": "SwitchAlert",
        "icon_emoji": ":bulb:",
        "channel": "#webhook-testing",
        "attachments": [
            {
                "color": color,
                "fields": [
                    {
                        "title": title,
                        "value": message,
                        "short": "false",
                    }
                ]
            }
        ]
    }

    headers = {
        'Content-Type': "application/json",
    }

    response = requests.post(
        webhook_url,
        data=json.dumps(slack_data),
        headers=headers
    )

    print(response.text)
        
@app.route('/webhook', methods=['POST'])
def webhook():
    if request.method == 'POST':

        print('Got Webhook')

        vlanFlag, switchList = fileChanges(request.json['commits'])
        if vlanFlag or switchList:

            print('Change found')

            updateRepo()
            status, stdoutPath = runAnsible(vlanFlag, switchList)
            sendAlert(vlanFlag, switchList, status, stdoutPath)
        return 'success', 200
        
    else:
        abort(400)

if __name__ == '__main__':
    app.run()
