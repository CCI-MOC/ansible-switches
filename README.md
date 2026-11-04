# Ansible Site for Managing MOC Switches

Ansible site for MOC switches, uses [cci_moc.stateful_switches](https://galaxy.ansible.com/ui/repo/published/cci_moc/stateful_switches/) for stateful configuration. This repository holds the inventory and the switch
manifests.

## Site Setup

1. Install newest version of ansible
1. Install required PyPI packages:
    1. `pip install --user ansible-pylibssh`
1. Install the required ansible collections: `ansible-galaxy collection install -r requirements.yaml`

## Configuration

The VLAN manifest is in `group_vars/all/vlans.yaml`, and the interface manifest of each
switch is in `host_vars/<switch>/interfaces.yaml`. The fields, and which switch OSes
support each of them, are listed in the
[collection README](https://github.com/CCI-MOC/ansible-stateful-switches#manifest-fields).

## MOC Specific Documentation

Every switch that exists in the MOCA system exists in this ansible site's host file. This project does not support all NOS types yet so some don't have individual host vars but exist in the hosts file for documentation sake.

### Naming Convention

Network equipment addresses follow a common convention. Each list item represents one octet of an IPv4 address:

* `10.`
* `80` for MOC
* `[1,2]` 1 for core networking, 2 for rack networking
* `10*<rack number> + unit number` Each rack gets its own unique number. For example, 3 switches in a rack might be `21`, `22`, and `23`
