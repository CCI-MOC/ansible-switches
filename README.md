# Ansible Site for Managing MOC/OCT Switches

Ansible site for MOC/OCT switches

## Supported Switch OSes

* Dell OS9 (FTOS9)
* Cisco NXOS
* NVIDIA Cumulus Linux 5.X (NVUE)
* PicOS 8 (WIP)

## Site Setup

1. Install newest version of ansible
1. Install required PyPI packages:
    1. `pip install --user ansible-pylibssh`
1. Install the required ansible modules: `ansible-galaxy collection install -r requirements.yaml`
1. Set up AWS CLI and be sure you can access the correct secrets
1. On your client, you may have to enable legacy kex algorithms for some switches:

    ```
    KexAlgorithms +diffie-hellman-group1-sha1,diffie-hellman-group14-sha1
    ```

## Interface Parameter Compatibility Matrix

| Parameter | Dell OS9 | Cisco NXOS | Cumulus Linux 5 | PicOS 8 |
|-----------|----------|------------|-----------------|---------|
| description | ✅ | ✅ | ✅ | ❌ |
| state | ✅ | ✅ | ✅ | ❌ |
| mtu | ✅ | ✅ | ✅ | ❌ |
| fec | ✅ | ✅ | ✅ | ❌ |
| autoneg | ✅ | ✅ | ✅ | ❌ |
| speed | ❌ | ✅ | ✅ | ❌ |
| stp/edgeport | ✅ | ✅ | ✅ | ❌ |
| stp/bpduguard | ✅ | ✅ | ✅ | ❌ |
| stp/rootguard | ✅ | ✅ | ✅ | ❌ |
| stp/disabled | ✅ | ✅ | ✅ | ❌ |
| fanout | ✅ | ✅ | ✅ | ❌ |
| managed | ❌ | ✅ | ✅ | ❌ |
| allowlist | ✅ | ❌ | ❌ | ❌ |
| blocklist | ✅ | ❌ | ❌ | ❌ |
| portmode | ✅ | ✅ | ✅ | ❌ |
| untagged | ✅ | ✅ | ✅ | ❌ |
| tagged | ✅ | ✅ | ✅ | ❌ |
| ip4 | ✅ | ✅ | ✅ | ❌ |
| ip6 | ✅ | ✅ | ✅ | ❌ |
| gateway | ❌ | ❌ | ✅ | ❌ |
| lag-members | ✅ | ✅ | ✅ | ❌ |
| lacp-members-active | ✅ | ✅ | ✅ | ❌ |
| lacp-members-passive | ✅ | ✅ | ❌ | ❌ |
| lacp-rate | ✅ | ✅ | ✅ | ❌ |
| lacp-bypass | ❌ | ❌ | ✅ | ❌ |
| mlag | ✅ | ✅ | ✅ | ❌ |

## Configuration

### Available Fields (VLAN)

* `id` VLAN 802.1q tag (Integer)
* `name` Identifying name for the vlan (String)
* `description` Description for the vlan (String)
* `managed` Don't create nor destroy this VLAN (Boolean)
* `fabrics` Define fabrics on which vlans can exist (ansible host groups) (List of strings)

### Available Fields (Interface)

* `description` Sets the description of the interface. (String)
* `state` Sets the admin state of the mode ("up", or "down")
* `mtu` Sets the MTU of the interface (Integer 576-9416, 552-9216 on Cumulus)
* `fec` If false, forward-error-correction is disabled on the interface (Boolean)
* `autoneg` If false, auto-negotiation is disabled on the interface (Boolean)
* `speed` Sets the link speed of the interface (Cumulus: String such as `"25G"`, `"200G"`, `"100M"` or `"auto"`)
* `stp` Sets STP Parameters
  * `edgeport` Sets whether port should be an edge port (Boolean)
  * `bpduguard` Enables BPDUguard on an interface (Boolean)
  * `rootguard` Enables Rootguard on an interface (Boolean)
  * `disabled` Disables STP on the interface (Boolean)
* `fanout` Sets fanout configuration
  * `mode` Sets mode (`single`, `dual`, or `quad`)
  * `speed` Sets the fanout speed (`10G`, `25G`, or `40G`)
* `managed` If true, this interface will not be configured by ansible. Works for both VLANs and interfaces (Boolean)
* `allowlist` Only allow modification of these fields (List of Strings)
* `blocklist` Block modification of these fields (List of Strings)
* `portmode` L2 portmode of an interface (String "access", "trunk", or "hybrid")
* `untagged` Single vlan to untag, requires portmode access or hybrid (Integer 2-4094)
* `tagged` List of vlans to tag, requires portmode trunk or hybrid (List of Integers 2-4094)
* `ip4` Sets the IPv4 address of the interface (String "X.X.X.X/YY")
* `ip6` Sets the IPv6 address of the interface (String)
* `gateway` Sets the default gateway of the interface (String, or List of Strings for an IPv4 and an IPv6 gateway)
* `lag-members` List of non-LACP lag members for a port channel (List of Strings, interface names)
* `lacp-members-active` List of LACP active members for a port channel (List of Strings, interface names)
* `lacp-members-passive` List of LACP passive members for a port channel (List of Strings, interface names)
* `lacp-rate` Sets the switch rate for LACP only (String "fast" or "slow")
* `lacp-bypass` If true, an LACP port channel forwards before LACP comes up, for example while a server PXE boots (Boolean)
* `mlag` Set the label of the peer port-channel for a paired switch (String interface name)

## MOC Specific Documentation

Every switch that exists in the MOCA system exists in this ansible site's host file. This project does not support all NOS types yet so some don't have individual host vars but exist in the hosts file for documentation sake.

### Naming Convention

Network equipment addresses follow a common convention. Each list item represents one octet of an IPv4 address:

* `10.`
* `[80,81]` 80 for MOC/NERC, 81 for OCT
* `[1,2]` 1 for core networking, 2 for rack networking
* `10*<rack number> + unit number` Each rack gets its own unique number. For example, 3 switches in a rack might be `21`, `22`, and `23`
