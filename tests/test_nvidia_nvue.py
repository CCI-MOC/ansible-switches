import copy
import ipaddress
import json
import pathlib

import pytest

import nvidia_nvue

FILES = pathlib.Path(__file__).parent / "nvidia_nvue_files"

VERSION_511 = (5, 11)
VERSION_515 = (5, 15)

BRIDGE_VLANS = {10, 20, 21, 22, 30}

# Bonds and SVIs in applied_config.json, which are removed when they are not in
# the manifest
APPLIED_REMOVABLE = ["bond1", "bond3", "vlan10", "vlan30"]


@pytest.fixture
def applied_config():
    """Output of "nv config show -o json" on an MLAG leaf running 5.11"""
    with open(FILES / "applied_config.json") as f:
        return json.load(f)


@pytest.fixture
def combined_config():
    """GPU-CORE-1 on 5.11, where NVUE combines interfaces that share settings"""
    with open(FILES / "applied_config_combined.json") as f:
        return json.load(f)


@pytest.fixture
def vlans():
    return {
        10: {"name": "SERVERS", "description": "Servers"},
        20: {"name": "STORAGE", "description": "Storage"},
        40: {"name": "EXISTING-MANAGED", "description": "Managed", "managed": True},
        50: {"name": "MISSING-MANAGED", "description": "Managed", "managed": True},
    }


def withVersion(applied_config, version):
    applied_config = copy.deepcopy(applied_config)
    applied_config[0]["header"]["version"] = version
    return applied_config


def unsetInterfaces(*intfs):
    """The unset command for applied_config.json: the given manifest interfaces,
    then the bonds and SVIs that are not among them"""
    removed = [intf for intf in APPLIED_REMOVABLE if intf not in intfs]
    return f"nv unset interface {','.join(list(intfs) + removed)}"


def generate(
    fields,
    intf="swp1",
    intf_type="swp",
    version=VERSION_511,
    bond_members=frozenset(),
    bridge_vlans=BRIDGE_VLANS,
):
    """Shortcut for generateINTFCommands with a bridge that has vlans 10, 20-22 and 30"""
    return nvidia_nvue.generateINTFCommands(
        intf, fields, intf_type, bond_members, bridge_vlans, version
    )


def fanout(fields, version=None, intf="swp5"):
    """Shortcut for generateFanoutCommands"""
    lanes = nvidia_nvue.getFanoutLanes(intf, fields)
    return nvidia_nvue.generateFanoutCommands(intf, fields, lanes, version)


#
# Parsing the applied configuration
#


def test_getAppliedConfig(applied_config):
    res = nvidia_nvue.getAppliedConfig(applied_config)
    assert res["system"]["hostname"] == "leaf01"
    assert "swp1" in res["interface"]


# fmt: off
@pytest.mark.parametrize(
    "applied, expected",
    [
        ({"set": {"system": {"hostname": "a"}}},    {"system": {"hostname": "a"}}),
        ([{"header": {}}, {"set": None}],           {}),
        ([{"header": {}}],                          {}),
        ([],                                        {}),
        ("",                                        {}),
        (None,                                      {}),
    ],
)
def test_getAppliedConfig_formats(applied, expected):
    assert nvidia_nvue.getAppliedConfig(applied) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "applied, expected",
    [
        ({"set": {}},                               [{"set": {}}]),
        ([{"header": {}}, "junk", None, {"set": {}}], [{"header": {}}, {"set": {}}]),
        ([],                                        []),
        ("[]",                                      []),
        (None,                                      []),
    ],
)
def test_getAppliedDocs(applied, expected):
    assert nvidia_nvue.getAppliedDocs(applied) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "key, expected",
    [
        ("swp1",                ["swp1"]),
        ("swp1-2",              ["swp1", "swp2"]),
        ("bond1,11",            ["bond1", "bond11"]),
        ("bond1,swp1-2,29-30",  ["bond1", "swp1", "swp2", "swp29", "swp30"]),
        ("swp1-2,29-30",        ["swp1", "swp2", "swp29", "swp30"]),
        ("swp1s0-3",            ["swp1s0", "swp1s1", "swp1s2", "swp1s3"]),
        ("swp1s0-1,swp2s0",     ["swp1s0", "swp1s1", "swp2s0"]),
        ("vlan2304",            ["vlan2304"]),
        ("peerlink.4094",       ["peerlink.4094"]),
        ("peerlink",            ["peerlink"]),
        ("lo",                  ["lo"]),
        ("eth0",                ["eth0"]),
    ],
)
def test_expandInterfaceKey(key, expected):
    assert nvidia_nvue.expandInterfaceKey(key) == expected
# fmt: on


def test_mergeConfig():
    base = {"link": {"state": {"up": {}}}, "type": "swp"}
    extra = {"link": {"mtu": 9216}, "type": "bond"}
    assert nvidia_nvue.mergeConfig(base, extra) == {
        "link": {"state": {"up": {}}, "mtu": 9216},
        "type": "bond",
    }
    # base is not modified
    assert base == {"link": {"state": {"up": {}}}, "type": "swp"}


def test_getAppliedInterfaces(combined_config):
    applied_cfg = nvidia_nvue.getAppliedConfig(combined_config)
    res = nvidia_nvue.getAppliedInterfaces(applied_cfg)

    assert set(res) == {
        "bond1",
        "bond11",
        "eth0",
        "peerlink",
        "peerlink.4094",
        "swp1",
        "swp2",
        "swp29",
        "swp30",
        "vlan2304",
    }
    assert res["swp1"] == {
        "link": {"state": {"up": {}}, "fec": "rs", "speed": "200G"},
        "type": "swp",
    }
    assert res["swp29"] == {"link": {"state": {"up": {}}}, "type": "swp"}
    # bond1 is spread over "bond1", "bond1,11" and "bond1,swp1-2,29-30"
    assert res["bond1"] == {
        "bond": {
            "member": {"swp31": {}, "swp32": {}},
            "mlag": {"id": 1, "enable": "on"},
            "lacp-rate": "fast",
        },
        "bridge": {"domain": {"br_default": {"stp": {"bpdu-filter": "on"}}}},
        "description": "NERC-CORE uplink",
        "link": {"state": {"up": {}}},
        "type": "bond",
    }
    assert res["bond11"]["type"] == "bond"


def test_getAppliedInterfaces_conflict():
    # NVUE doesn't put one setting under two keys, if it did the later key wins
    applied_cfg = {
        "interface": {
            "swp1-2": {"link": {"mtu": 1500, "state": {"up": {}}}},
            "swp2": {"link": {"mtu": 9216}},
        }
    }
    res = nvidia_nvue.getAppliedInterfaces(applied_cfg)
    assert res == {
        "swp1": {"link": {"mtu": 1500, "state": {"up": {}}}},
        "swp2": {"link": {"mtu": 9216, "state": {"up": {}}}},
    }


# fmt: off
@pytest.mark.parametrize(
    "version, expected",
    [
        ("Cumulus Linux 5.11.0",    (5, 11)),
        ("Cumulus Linux 5.15.1",    (5, 15)),
        ("Cumulus Linux 6.0.0",     (6, 0)),
        ("",                        None),
    ],
)
def test_getNVUEVersion(applied_config, version, expected):
    res = nvidia_nvue.getNVUEVersion(withVersion(applied_config, version))
    assert res == expected
# fmt: on


def test_getNVUEVersion_no_header():
    assert nvidia_nvue.getNVUEVersion([{"set": {}}]) is None


# fmt: off
@pytest.mark.parametrize(
    "system, expected",
    [
        ({"build": "Cumulus Linux 5.1.0", "hostname": "a"},     (5, 1)),
        ({"build": None},                                       (5, 9)),
        ({"hostname": "a"},                                     (5, 9)),
        (None,                                                  (5, 9)),
    ],
)
def test_getNVUEVersion_system(applied_config, system, expected):
    # "nv show system" wins over the config header, which is the fallback
    applied = withVersion(applied_config, "Cumulus Linux 5.9.1")
    assert nvidia_nvue.getNVUEVersion(applied, system) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "version, minimum, expected",
    [
        ((5, 1),    (5, 3),     False),
        ((5, 3),    (5, 3),     True),
        ((5, 18),   (5, 15),    True),
        ((6, 0),    (5, 15),    True),
        (None,      (5, 4),     True),
        (None,      (5, 15),    False),
    ],
)
def test_releaseAtLeast(version, minimum, expected):
    assert nvidia_nvue.releaseAtLeast(version, minimum) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "value, version, expected",
    [
        (True,  (5, 11),    "on"),
        (False, (5, 11),    "off"),
        (True,  (5, 14),    "on"),
        (True,  (5, 15),    "enabled"),
        (False, (5, 18),    "disabled"),
        (True,  (6, 0),     "enabled"),
        (True,  None,       "on"),
    ],
)
def test_nvueBool(value, version, expected):
    assert nvidia_nvue.nvueBool(value, version) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "version, expected",
    [
        ((5, 9),    "enable on"),
        ((5, 10),   "state enabled"),
        (None,      "state enabled"),
    ],
)
def test_nvueEnable(version, expected):
    assert nvidia_nvue.nvueEnable(version, (5, 10)) == expected
# fmt: on


#
# Manifest values
#


@pytest.mark.parametrize("value", [9216, "9216", 552])
def test_getInt(value):
    assert nvidia_nvue.getInt("swp1", "mtu", value, 552, 9216) == int(value)


@pytest.mark.parametrize("value", [True, 9216.0, "9k", -1, 551, 9217, None, "9216 "])
def test_getInt_invalid(value):
    with pytest.raises(
        ValueError, match="^swp1: mtu must be a number from 552 to 9216"
    ):
        nvidia_nvue.getInt("swp1", "mtu", value, 552, 9216)


# fmt: off
@pytest.mark.parametrize(
    "value, ip_version, expected",
    [
        ("10.0.0.1/31",         4,      ipaddress.ip_interface("10.0.0.1/31")),
        ("2001:db8::1/64",      6,      ipaddress.ip_interface("2001:db8::1/64")),
        ("10.0.0.1",            None,   ipaddress.ip_address("10.0.0.1")),
        ("2001:db8::fe",        None,   ipaddress.ip_address("2001:db8::fe")),
    ],
)
def test_getIPAddress(value, ip_version, expected):
    assert nvidia_nvue.getIPAddress("swp1", "ip", value, ip_version) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "value, ip_version, error",
    [
        # a bare address or a netmask instead of a prefix length
        ("10.0.0.1",                    4,      "IPv4 address with a prefix length"),
        ("10.0.0.1/255.255.255.0",      4,      "IPv4 address with a prefix length"),
        ("10.0.0.1/33",                 4,      "IPv4 address with a prefix length"),
        ("2001:db8::1/64",              4,      "IPv4 address with a prefix length"),
        ("10.0.0.1/24",                 6,      "IPv6 address with a prefix length"),
        ("2001:db8::1/64; reboot",      6,      "IPv6 address with a prefix length"),
        ("10.0.0.300",                  None,   "not a valid IP address"),
        ("10.0.0.1/24",                 None,   "not a valid IP address"),
    ],
)
def test_getIPAddress_invalid(value, ip_version, error):
    with pytest.raises(ValueError, match=error):
        nvidia_nvue.getIPAddress("swp1", "ip", value, ip_version)
# fmt: on


#
# vlan lists
#


# fmt: off
@pytest.mark.parametrize(
    "vlan_keys, expected",
    [
        (["10"],                {10}),
        (["10,20,30"],          {10, 20, 30}),
        (["30-32"],             {30, 31, 32}),
        (["10,20-22", "40"],    {10, 20, 21, 22, 40}),
        ([10],                  {10}),
        (["10,", "none"],       {10}),
        ([],                    set()),
    ],
)
def test_parseVLANList(vlan_keys, expected):
    assert nvidia_nvue.parseVLANList(vlan_keys) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "vlan_ids, expected",
    [
        ([10],                      "10"),
        ([20, 10],                  "10,20"),
        ([10, 11, 12, 20],          "10-12,20"),
        ([1, 3, 4, 6, 7, 8, 10],    "1,3-4,6-8,10"),
        ([],                        ""),
    ],
)
def test_compressVLANList(vlan_ids, expected):
    assert nvidia_nvue.compressVLANList(vlan_ids) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "tagged, expected",
    [
        ([30],              {30}),
        ([30, "40:42"],     {30, 40, 41, 42}),
        ("50:51",           {50, 51}),
        (["60"],            {60}),
        (60,                {60}),
        ([],                set()),
    ],
)
def test_getTaggedVLANs(tagged, expected):
    assert nvidia_nvue.getTaggedVLANs("swp1", tagged) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "tagged, error",
    [
        (["10:20:30"],  "tagged item 10:20:30 is not a vlan"),
        (["ten"],       "tagged must be a number"),
        (["10:x"],      "tagged must be a number"),
        ([True],        "tagged must be a number"),
        ([0],           "tagged must be a number"),
        ([4095],        "tagged must be a number"),
    ],
)
def test_getTaggedVLANs_invalid(tagged, error):
    with pytest.raises(ValueError, match=f"^swp1: {error}"):
        nvidia_nvue.getTaggedVLANs("swp1", tagged)
# fmt: on


#
# Interface helpers
#


# fmt: off
@pytest.mark.parametrize(
    "mlag, expected",
    [
        ("bond10",          10),
        ("Port-channel 20", 20),
        (7,                 7),
        ("8",               8),
    ],
)
def test_getMLAGID(mlag, expected):
    assert nvidia_nvue.getMLAGID("bond1", mlag) == expected
# fmt: on


@pytest.mark.parametrize("mlag", ["peerlink", True, 0, 70000])
def test_getMLAGID_invalid(mlag):
    with pytest.raises(ValueError):
        nvidia_nvue.getMLAGID("bond1", mlag)


# fmt: off
@pytest.mark.parametrize(
    "fields, expected",
    [
        ({},                    False),
        ({"mlag": None},        False),
        ({"mlag": False},       False),
        ({"mlag": ""},          False),
        # 0 is set, so that getMLAGID rejects it
        ({"mlag": 0},           True),
        ({"mlag": "bond1"},     True),
        ({"mlag": 5},           True),
    ],
)
def test_hasMLAG(fields, expected):
    assert nvidia_nvue.hasMLAG(fields) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "intf, fields, applied_interfaces, expected",
    [
        ("vlan10",  {},                             {},                                 "svi"),
        ("bond1",   {},                             {},                                 "bond"),
        ("uplink",  {"lacp-members-active": []},    {},                                 "bond"),
        ("uplink",  {},                             {"uplink": {"type": "bond"}},       "bond"),
        ("swp1",    {},                             {"swp1": {"type": "swp"}},          "swp"),
        ("swp1s0",  {"portmode": "access"},         {},                                 "swp"),
    ],
)
def test_getInterfaceType(intf, fields, applied_interfaces, expected):
    res = nvidia_nvue.getInterfaceType(intf, fields, applied_interfaces)
    assert res == expected
# fmt: on


@pytest.mark.parametrize(
    "intf", ["swp1", "swp1s0", "bond10", "vlan2304", "peerlink.4094", "uplink_a"]
)
def test_checkInterfaceName(intf):
    nvidia_nvue.checkInterfaceName(intf)


@pytest.mark.parametrize("intf", ["Port-channel 10", "swp1;reboot", "hundredGigE 1/1"])
def test_checkInterfaceName_invalid(intf):
    with pytest.raises(ValueError, match="not a valid"):
        nvidia_nvue.checkInterfaceName(intf)


@pytest.mark.parametrize("intf", ["swp1-4", "swp1s0-3", "bond1-2", "vlan10-20"])
def test_checkInterfaceName_range(intf):
    with pytest.raises(ValueError, match="ranges are not allowed"):
        nvidia_nvue.checkInterfaceName(intf)


# fmt: off
@pytest.mark.parametrize(
    "intf, expected",
    [
        ("swp1s0",  "swp1"),
        ("swp10s3", "swp10"),
        ("swp1",    None),
        ("bond1",   None),
    ],
)
def test_getFanoutParent(intf, expected):
    assert nvidia_nvue.getFanoutParent(intf) == expected
# fmt: on


def test_getBondMembers(applied_config):
    applied_interfaces = nvidia_nvue.getAppliedConfig(applied_config)["interface"]
    interfaces = {
        "bond1": {"lacp-members-active": ["swp2"]},
        "bond2": {"lag-members": ["swp4"]},
        "bond9": {"managed": True, "lag-members": ["swp9"]},
    }
    res = nvidia_nvue.getBondMembers(interfaces, applied_interfaces)
    # bond1 is rebuilt with its manifest members, bond3 is not in the manifest so it
    # is removed, and the peerlink keeps its applied members
    assert res == {"swp2", "swp4", "swp49", "swp50"}


def test_getBondMembers_managed(applied_config):
    applied_interfaces = nvidia_nvue.getAppliedConfig(applied_config)["interface"]
    interfaces = {"bond1": {"managed": True, "lag-members": ["swp9"]}}
    res = nvidia_nvue.getBondMembers(interfaces, applied_interfaces)
    assert res == {"swp1", "swp49", "swp50"}


def test_getBondMembers_combined_members():
    applied_interfaces = {"bond5": {"bond": {"member": {"swp10-11,14": {}}}}}
    res = nvidia_nvue.getBondMembers({"bond5": {"managed": True}}, applied_interfaces)
    assert res == {"swp10", "swp11", "swp14"}


def test_groupCommands():
    intf_cmds = {
        "swp1": ["type swp", "link mtu 9216", "description a"],
        "swp2": ["type swp", "link mtu 9216", "description b"],
        "bond1": ["type bond", "link mtu 9216"],
    }
    # the types go first
    assert nvidia_nvue.groupCommands(intf_cmds) == [
        "nv set interface swp1,swp2 type swp",
        "nv set interface bond1 type bond",
        "nv set interface swp1,swp2,bond1 link mtu 9216",
        "nv set interface swp1 description a",
        "nv set interface swp2 description b",
    ]


#
# Manifest checks
#


def test_checkManifest():
    interfaces = {
        "swp1": {},
        "swp2": {"managed": True},
        "swp3": {"fanout": {"type": "single"}},
        "swp5": {"fanout": {"type": "dual"}},
        "swp5s0": {},
        "swp5s1": {"managed": True},
        # a managed child port is left alone, so it doesn't need its parent
        "swp7s0": {"managed": True},
        "bond1": {"lacp-members-active": ["swp1", "swp2", "swp3", "swp5s0"]},
        "swp1.100": {"managed": True},
        "uplink": {},
        "vlan10": {},
    }
    applied_interfaces = {"swp1.100": {"type": "sub"}, "uplink": {"type": "bond"}}
    nvidia_nvue.checkManifest(interfaces, applied_interfaces)


# fmt: off
@pytest.mark.parametrize(
    "interfaces, applied_interfaces, error",
    [
        # managed fanout parent
        ({"swp5": {"managed": True, "fanout": {"type": "dual"}}},       {},     "swp5: a fanout parent port cannot be managed"),
        # interfaces without manifest fields
        ({"swp1.100": {}},                                              {},     "swp1.100: the driver only configures"),
        ({"swp1.100": {}},                                              {"swp1.100": {"type": "sub"}},  "swp1.100: the driver only configures"),
        ({"tunnel1": {"state": "up"}},                                  {"tunnel1": {"type": "tunnel"}},    "tunnel1: the driver only configures"),
        ({"tunnel1": {"state": "up"}},                                  {},     "tunnel1: the driver only configures"),
        ({"br_default": {}},                                            {"br_default": {"type": "bridge"}}, "br_default: the driver only configures"),
        # breakouts
        ({"swp5": {"fanout": {"type": "dual"}}, "swp5s0": {}},          {},     "swp5: the fanout needs manifest entries for swp5s1$"),
        ({"swp5s0": {}},                                                {},     "swp5s0: breakout port needs its parent swp5"),
        ({"swp5": {"managed": True}, "swp5s0": {}},                     {},     "swp5s0: breakout port needs its parent swp5"),
        ({"swp5": {"state": "up"}, "swp5s0": {}},                       {},     "swp5s0: breakout port needs its parent swp5"),
        ({"swp5": {"fanout": {"type": "dual"}}, "swp5s0": {}, "swp5s1": {}, "swp5s2": {}},  {},     "swp5s2: the swp5 fanout does not create this port"),
        # bond members
        ({"bond1": {"lacp-members-active": ["swp1"]}},                  {},     "bond1: bond member swp1 needs its own manifest entry"),
        ({"bond1": {"lag-members": ["swp1"]}},                          {},     "bond1: bond member swp1 needs its own manifest entry"),
        ({"bond1": {"lacp-members-active": ["swp1-2"]}},                {},     "swp1-2: interface ranges are not allowed"),
        ({"bond1": {"lacp-members-active": ["hundredGigE 1/1"]}},       {},     "hundredGigE 1/1: not a valid"),
        ({"bond1": {"lacp-members-active": ["eth0"]}},                  {},     "bond1: eth0 cannot be a bond member"),
        ({"bond1": {"lacp-members-active": ["swp5"]}, "swp5": {"fanout": {"type": "quad"}}, "swp5s0": {}, "swp5s1": {}, "swp5s2": {}, "swp5s3": {}},  {},  "bond1: bond member swp5 is broken out"),
    ],
)
def test_checkManifest_invalid(interfaces, applied_interfaces, error):
    with pytest.raises(ValueError, match=f"^{error}"):
        nvidia_nvue.checkManifest(interfaces, applied_interfaces)
# fmt: on


#
# Single interface commands
#


@pytest.mark.parametrize(
    "fields, expected",
    [
        ({}, ["type swp"]),
        (
            {"description": "Server 1 port 1", "state": "up", "mtu": 9216},
            [
                "type swp",
                "description 'Server 1 port 1'",
                "link state up",
                "link mtu 9216",
            ],
        ),
        ({"state": "down"}, ["type swp", "link state down"]),
        ({"description": "it's"}, ["type swp", "description 'it'\"'\"'s'"]),
        # a description without a value leaves the port without one
        ({"description": None}, ["type swp"]),
        ({"mtu": "1500"}, ["type swp", "link mtu 1500"]),
        ({"autoneg": True}, ["type swp", "link auto-negotiate on"]),
        ({"autoneg": False}, ["type swp", "link auto-negotiate off"]),
        ({"fec": False}, ["type swp", "link fec off"]),
        ({"fec": "off"}, ["type swp", "link fec off"]),
        ({"fec": "rs"}, ["type swp", "link fec rs"]),
        ({"fec": "BaseR"}, ["type swp", "link fec baser"]),
        ({"fec": "auto"}, ["type swp", "link fec auto"]),
        ({"speed": "200G"}, ["type swp", "link speed 200G"]),
        ({"speed": "25g"}, ["type swp", "link speed 25G"]),
        ({"speed": "100M"}, ["type swp", "link speed 100M"]),
        ({"speed": "auto"}, ["type swp", "link speed auto"]),
        (
            {"autoneg": False, "speed": "100G", "fec": "rs"},
            [
                "type swp",
                "link auto-negotiate off",
                "link speed 100G",
                "link fec rs",
            ],
        ),
        (
            {"portmode": "access", "untagged": 10},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default access 10",
            ],
        ),
        (
            {"portmode": "access"},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default access 1",
            ],
        ),
        (
            {"portmode": "trunk", "tagged": [10, "20:21"]},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default vlan 10,20-21",
                "bridge domain br_default untagged none",
            ],
        ),
        # "all" leaves out the vlan list, so NVUE carries every bridge vlan
        (
            {"portmode": "trunk", "tagged": "all"},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default untagged none",
            ],
        ),
        (
            {"portmode": "hybrid", "tagged": "all", "untagged": "10"},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default untagged 10",
            ],
        ),
        (
            {"portmode": "hybrid", "tagged": [20], "untagged": 10},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default vlan 10,20",
                "bridge domain br_default untagged 10",
            ],
        ),
        # without tagged vlans a hybrid port only carries its untagged vlan
        (
            {"portmode": "hybrid", "untagged": 10},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default vlan 10",
                "bridge domain br_default untagged 10",
            ],
        ),
        (
            {"portmode": "hybrid", "tagged": [], "untagged": 10},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default vlan 10",
                "bridge domain br_default untagged 10",
            ],
        ),
        (
            {
                "portmode": "access",
                "untagged": 10,
                "stp": {
                    "edgeport": True,
                    "bpduguard": True,
                    "rootguard": True,
                    "disabled": True,
                },
            },
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default access 10",
                "bridge domain br_default stp admin-edge on",
                "bridge domain br_default stp bpdu-guard on",
                "bridge domain br_default stp restrrole on",
                "bridge domain br_default stp bpdu-filter on",
            ],
        ),
        (
            {"portmode": "access", "untagged": 10, "stp": {"edgeport": False}},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default access 10",
            ],
        ),
        (
            {"portmode": "access", "untagged": 10, "stp": None},
            [
                "type swp",
                "bridge domain br_default",
                "bridge domain br_default access 10",
            ],
        ),
        # stp only applies to bridge ports
        (
            {"ip4": "10.0.0.1/31", "stp": {"rootguard": True}},
            ["type swp", "ip address 10.0.0.1/31"],
        ),
        (
            {"ip4": "10.0.0.1/31", "ip6": "2001:db8::1/127"},
            ["type swp", "ip address 10.0.0.1/31", "ip address 2001:db8::1/127"],
        ),
        (
            {"ip4": ["10.0.0.1/31", "10.0.1.1/31"]},
            ["type swp", "ip address 10.0.0.1/31", "ip address 10.0.1.1/31"],
        ),
        (
            {"ip4": "10.0.0.1/31", "gateway": "10.0.0.0"},
            ["type swp", "ip address 10.0.0.1/31", "ip gateway 10.0.0.0"],
        ),
    ],
)
def test_generateINTFCommands_swp(fields, expected):
    assert generate(fields) == expected


@pytest.mark.parametrize(
    "fields, version, expected",
    [
        (
            {"lacp-members-active": ["swp1"], "lacp-bypass": True},
            VERSION_511,
            ["type bond", "bond member swp1", "bond mode lacp", "bond lacp-bypass on"],
        ),
        (
            {"lacp-members-active": ["swp1"], "lacp-bypass": True},
            VERSION_515,
            [
                "type bond",
                "bond member swp1",
                "bond mode lacp",
                "bond lacp-bypass enabled",
            ],
        ),
        # off is the default, and static bonds do not run LACP
        (
            {"lacp-members-active": ["swp1"], "lacp-bypass": False},
            VERSION_511,
            ["type bond", "bond member swp1", "bond mode lacp"],
        ),
        (
            {"lag-members": ["swp1"], "lacp-bypass": True},
            VERSION_511,
            ["type bond", "bond member swp1", "bond mode static"],
        ),
    ],
)
def test_generateINTFCommands_lacp_bypass(fields, version, expected):
    assert (
        generate(fields, intf="bond10", intf_type="bond", version=version) == expected
    )


@pytest.mark.parametrize(
    "fields, expected",
    [
        (
            {"lacp-members-active": ["swp1", "swp2"]},
            ["type bond", "bond member swp1,swp2", "bond mode lacp"],
        ),
        (
            {"lag-members": ["swp1", "swp2"], "lacp-rate": "fast"},
            ["type bond", "bond member swp1,swp2", "bond mode static"],
        ),
        (
            {
                "description": "Server 1",
                "state": "up",
                "mtu": 9216,
                "autoneg": False,
                "fec": False,
                "speed": "100G",
                "lacp-members-active": ["swp1"],
                "lacp-rate": "slow",
                "mlag": "bond10",
                "portmode": "trunk",
                "tagged": "all",
                "stp": {"rootguard": True},
            },
            [
                "type bond",
                "description 'Server 1'",
                "link state up",
                "link mtu 9216",
                "bond member swp1",
                "bond mode lacp",
                "bond lacp-rate slow",
                "bond mlag id 10",
                "bond mlag enable on",
                "bridge domain br_default",
                "bridge domain br_default untagged none",
                "bridge domain br_default stp restrrole on",
            ],
        ),
        (
            {"mlag": 5},
            ["type bond", "bond mode lacp", "bond mlag id 5", "bond mlag enable on"],
        ),
        ({"mlag": False}, ["type bond", "bond mode lacp"]),
        ({"mlag": None}, ["type bond", "bond mode lacp"]),
    ],
)
def test_generateINTFCommands_bond(fields, expected):
    assert generate(fields, intf="bond10", intf_type="bond") == expected


def test_generateINTFCommands_svi():
    fields = {
        "description": "Servers",
        "state": "up",
        "mtu": 9000,
        "ip4": "10.1.10.1/24",
        "ip6": "2001:db8:10::1/64",
        "autoneg": False,
        "speed": "100G",
    }
    assert generate(fields, intf="vlan10", intf_type="svi") == [
        "type svi",
        "vlan 10",
        "description Servers",
        "link state up",
        "link mtu 9000",
        "ip address 10.1.10.1/24",
        "ip address 2001:db8:10::1/64",
    ]


@pytest.mark.parametrize(
    "version, expected",
    [
        (
            (5, 14),
            [
                "ip address 10.1.10.1/24",
                "ip address 2001:db8:10::1/64",
                "ip gateway 10.1.10.254",
                "ip gateway 2001:db8:10::fe",
            ],
        ),
        # 5.15 split ip into ipv4 and ipv6
        (
            (5, 15),
            [
                "ipv4 address 10.1.10.1/24",
                "ipv6 address 2001:db8:10::1/64",
                "ipv4 gateway 10.1.10.254",
                "ipv6 gateway 2001:db8:10::fe",
            ],
        ),
    ],
)
def test_generateINTFCommands_ip(version, expected):
    fields = {
        "ip4": "10.1.10.1/24",
        "ip6": "2001:db8:10::1/64",
        "gateway": ["10.1.10.254", "2001:db8:10::fe"],
    }
    res = generate(fields, intf="vlan10", intf_type="svi", version=version)
    assert res == ["type svi", "vlan 10"] + expected


def test_generateINTFCommands_515():
    fields = {
        "autoneg": True,
        "portmode": "access",
        "untagged": 10,
        "stp": {"edgeport": True, "bpduguard": True},
    }
    assert generate(fields, version=VERSION_515) == [
        "type swp",
        "link auto-negotiate enabled",
        "bridge domain br_default",
        "bridge domain br_default access 10",
        "bridge domain br_default stp admin-edge enabled",
        "bridge domain br_default stp bpdu-guard enabled",
    ]


def test_generateINTFCommands_515_mlag():
    fields = {"lacp-members-active": ["swp1"], "mlag": "bond10"}
    assert generate(fields, intf="bond10", intf_type="bond", version=VERSION_515) == [
        "type bond",
        "bond member swp1",
        "bond mode lacp",
        "bond mlag id 10",
        "bond mlag state enabled",
    ]


# fmt: off
@pytest.mark.parametrize(
    "intf, intf_type, fields, kwargs, error",
    [
        ("swp1", "swp", {"state": "enabled"}, {}, "state must be"),
        ("swp1", "swp", {"mtu": 9416}, {}, "mtu must be a number from 552 to 9216"),
        ("swp1", "swp", {"mtu": "jumbo"}, {}, "mtu must be a number"),
        ("swp1", "swp", {"mtu": True}, {}, "mtu must be a number"),
        ("swp1", "swp", {"fec": True}, {}, "fec must be"),
        ("swp1", "swp", {"fec": "fc"}, {}, "fec must be"),
        ("swp1", "swp", {"speed": 10000}, {}, "speed must be"),
        ("swp1", "swp", {"speed": "fast"}, {}, "speed must be"),
        ("swp1", "swp", {"gateway": "10.0.0.300"}, {}, "gateway 10.0.0.300 is not a valid IP address"),
        ("swp1", "swp", {"ip4": "10.0.0.1"}, {}, "ip4 must be an IPv4 address with a prefix length"),
        ("swp1", "swp", {"ip4": "2001:db8::1/64"}, {}, "ip4 must be an IPv4 address"),
        ("swp1", "swp", {"ip6": "10.0.0.1/24"}, {}, "ip6 must be an IPv6 address"),
        ("swp1", "swp", {"portmode": "access", "gateway": "10.0.0.1"}, {}, "an L2 port"),
        ("swp1", "swp", {"portmode": "tagged"}, {}, "portmode must be"),
        ("swp1", "swp", {"untagged": 10}, {}, "tagged and untagged require a portmode"),
        ("swp1", "swp", {"tagged": [10]}, {}, "tagged and untagged require a portmode"),
        ("swp1", "swp", {"portmode": "access", "tagged": [10]}, {}, "access ports"),
        ("swp1", "swp", {"portmode": "access", "untagged": "ten"}, {}, "untagged must be a number"),
        ("swp1", "swp", {"portmode": "access", "untagged": 0}, {}, "untagged must be a number"),
        ("swp1", "swp", {"portmode": "trunk", "untagged": 10}, {}, "trunk ports cannot have an untagged vlan"),
        ("swp1", "swp", {"portmode": "trunk", "tagged": ["x"]}, {}, "tagged must be a number"),
        ("swp1", "swp", {"portmode": "access", "untagged": 99}, {}, "vlans 99"),
        ("swp1", "swp", {"portmode": "hybrid", "tagged": "all", "untagged": 99}, {}, "vlans 99"),
        ("swp1", "swp", {"portmode": "trunk", "tagged": [10, 98, 99]}, {}, "vlans 98-99"),
        ("swp1", "swp", {"portmode": "trunk"}, {}, "the port carries no vlans"),
        ("swp1", "swp", {"portmode": "trunk", "tagged": []}, {}, "the port carries no vlans"),
        ("swp1", "swp", {"portmode": "hybrid"}, {}, "the port carries no vlans"),
        ("swp1", "swp", {"portmode": "access", "ip4": "10.0.0.1/24"}, {}, "an L2 port"),
        ("swp1", "swp", {"portmode": "access", "stp": True}, {}, "stp must be a mapping"),
        ("swp1", "swp", {"portmode": "access"}, {"bond_members": {"swp1"}}, "portmode is not supported"),
        ("swp1", "swp", {"mlag": "bond1"}, {}, "mlag is only supported on bonds"),
        ("swp1", "swp", {"mlag": 0}, {}, "mlag is only supported on bonds"),
        ("vlan10", "svi", {"portmode": "access"}, {}, "portmode is not supported"),
        ("vlan99", "svi", {}, {}, "vlans 99"),
        ("bond1", "bond", {"lag-members": ["swp1"], "lacp-members-active": ["swp2"]}, {}, "a bond cannot mix"),
        ("bond1", "bond", {"lacp-rate": "normal"}, {}, "lacp-rate must be"),
        ("bond1", "bond", {"lacp-members-passive": ["swp1"]}, {}, "lacp-members-passive"),
        ("bond1", "bond", {"mlag": 0}, {}, "MLAG ID 0 is outside"),
        ("uplink", "swp", {"lacp-members-passive": ["swp1"]}, {}, "lacp-members-passive"),
    ],
)
def test_generateINTFCommands_invalid(intf, intf_type, fields, kwargs, error):
    with pytest.raises(ValueError, match=f"^{intf}: {error}"):
        generate(fields, intf=intf, intf_type=intf_type, **kwargs)
# fmt: on


#
# Fanout
#


# fmt: off
@pytest.mark.parametrize(
    "fanout_fields, expected",
    [
        (
            {"type": "quad", "speed": "25G"},
            {
                "swp5": ["type swp", "link breakout 4x"],
                "swp5s0": ["type swp", "link speed 25G"],
                "swp5s1": ["type swp", "link speed 25G"],
                "swp5s2": ["type swp", "link speed 25G"],
                "swp5s3": ["type swp", "link speed 25G"],
            },
        ),
        (
            {"type": "dual", "speed": "50g"},
            {
                "swp5": ["type swp", "link breakout 2x"],
                "swp5s0": ["type swp", "link speed 50G"],
                "swp5s1": ["type swp", "link speed 50G"],
            },
        ),
        (
            {"type": "dual"},
            {"swp5": ["type swp", "link breakout 2x"]},
        ),
        (
            {"mode": "single", "speed": "40G"},
            {"swp5": ["type swp", "link breakout 1x", "link speed 40G"]},
        ),
    ],
)
@pytest.mark.parametrize("version", [(5, 3), (5, 11), (5, 18), None])
def test_generateFanoutCommands(fanout_fields, expected, version):
    assert fanout(fanout_fields, version) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "fanout_fields, expected",
    [
        # before 5.3 the lane speed is part of the breakout mode
        ({"type": "quad", "speed": "25G"},      {"swp5": ["type swp", "link breakout 4x25G"]}),
        ({"type": "quad", "speed": "10g"},      {"swp5": ["type swp", "link breakout 4x10G"]}),
        ({"type": "dual", "speed": "50G"},      {"swp5": ["type swp", "link breakout 2x50G"]}),
        ({"type": "octal", "speed": "50G"},     {"swp5": ["type swp", "link breakout 8x50G"]}),
        ({"type": "single", "speed": "40G"},    {"swp5": ["type swp", "link breakout 1x", "link speed 40G"]}),
        ({"type": "single"},                    {"swp5": ["type swp", "link breakout 1x"]}),
    ],
)
@pytest.mark.parametrize("version", [(5, 0), (5, 1), (5, 2)])
def test_generateFanoutCommands_pre53(fanout_fields, expected, version):
    assert fanout(fanout_fields, version) == expected
# fmt: on


# fmt: off
@pytest.mark.parametrize(
    "fanout_fields, version, expected",
    [
        # 5.2 added 2x10G and 2x25G
        ({"type": "dual", "speed": "25G"},  (5, 2),     "2x25G"),
        ({"type": "dual", "speed": "10G"},  (5, 2),     "2x10G"),
        ({"type": "dual", "speed": "25G"},  (5, 1),     None),
        ({"type": "dual", "speed": "10G"},  (5, 0),     None),
        ({"type": "quad", "speed": "auto"}, (5, 2),     None),
        ({"type": "octal", "speed": "25G"}, (5, 2),     None),
        ({"type": "quad", "speed": "40G"},  (5, 1),     None),
    ],
)
def test_generateFanoutCommands_pre53_modes(fanout_fields, version, expected):
    if expected:
        assert fanout(fanout_fields, version) == {"swp5": ["type swp", f"link breakout {expected}"]}
    else:
        with pytest.raises(ValueError, match=f"Cumulus Linux {version[0]}.{version[1]} has no"):
            fanout(fanout_fields, version)
# fmt: on


def test_generateFanoutCommands_pre53_no_speed():
    with pytest.raises(ValueError, match="needs a speed"):
        fanout({"type": "quad"}, (5, 1))


def test_generateFanoutCommands_invalid_speed():
    with pytest.raises(ValueError, match="speed must be"):
        fanout({"type": "quad", "speed": "25"})


@pytest.mark.parametrize("fanout_fields", [{"type": "triple"}, {}])
def test_getFanoutLanes_invalid(fanout_fields):
    with pytest.raises(ValueError, match="fanout type must be"):
        nvidia_nvue.getFanoutLanes("swp5", fanout_fields)


#
# Full configuration
#


def test_NVUE_GETCONFIG(applied_config, vlans):
    interfaces = {
        "swp1": {"description": "bond1 member", "state": "up"},
        "swp2": {
            "description": "Server 2",
            "state": "up",
            "mtu": 9216,
            "portmode": "access",
            "untagged": 10,
            "stp": {"edgeport": True, "bpduguard": True},
        },
        "swp5": {"fanout": {"type": "dual", "speed": "50G"}},
        "swp5s0": {
            "description": "Breakout 0",
            "state": "up",
            "portmode": "trunk",
            "tagged": [10, 20],
        },
        "swp5s1": {"state": "down"},
        "swp6": {"state": "down"},
        "bond1": {
            "description": "Server 1",
            "state": "up",
            "mtu": 9216,
            "lacp-members-active": ["swp1"],
            "lacp-rate": "fast",
            "mlag": "bond1",
            "portmode": "hybrid",
            "tagged": "all",
            "untagged": 20,
        },
        "vlan10": {"description": "Servers", "ip4": "10.1.10.2/24"},
        "eth0": {"managed": True},
    }

    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)

    assert res == [
        # 30 and 41 are not in the manifest, managed 40 exists and stays
        "nv unset bridge domain br_default vlan 30,41",
        # manifest interfaces, then bond3 and vlan30, which are not in the manifest.
        # swp5s2-3 are physical ports, so they are left alone
        "nv unset interface swp1,swp2,swp5,swp5s0,swp5s1,bond1,vlan10,bond3,vlan30",
        "nv set bridge domain br_default vlan 10,20",
        "nv set interface swp1,swp2,swp5,swp5s0,swp5s1,swp6 type swp",
        "nv set interface bond1 type bond",
        "nv set interface vlan10 type svi",
        "nv set interface swp1 description 'bond1 member'",
        "nv set interface swp1,swp2,swp5s0,bond1 link state up",
        "nv set interface swp2 description 'Server 2'",
        "nv set interface swp2,bond1 link mtu 9216",
        "nv set interface swp2,swp5s0,bond1 bridge domain br_default",
        "nv set interface swp2 bridge domain br_default access 10",
        "nv set interface swp2 bridge domain br_default stp admin-edge on",
        "nv set interface swp2 bridge domain br_default stp bpdu-guard on",
        "nv set interface swp5 link breakout 2x",
        "nv set interface swp5s0,swp5s1 link speed 50G",
        "nv set interface swp5s0 description 'Breakout 0'",
        "nv set interface swp5s0 bridge domain br_default vlan 10,20",
        "nv set interface swp5s0 bridge domain br_default untagged none",
        "nv set interface swp5s1,swp6 link state down",
        "nv set interface bond1 description 'Server 1'",
        "nv set interface bond1 bond member swp1",
        "nv set interface bond1 bond mode lacp",
        "nv set interface bond1 bond lacp-rate fast",
        "nv set interface bond1 bond mlag id 1",
        "nv set interface bond1 bond mlag enable on",
        "nv set interface bond1 bridge domain br_default untagged 20",
        "nv set interface vlan10 vlan 10",
        "nv set interface vlan10 description Servers",
        "nv set interface vlan10 ip address 10.1.10.2/24",
    ]


def test_NVUE_GETCONFIG_empty_switch(vlans):
    interfaces = {"swp1": {"portmode": "access", "untagged": 20}}
    res = nvidia_nvue.NVUE_GETCONFIG([], interfaces, vlans)
    assert res == [
        "nv set bridge domain br_default vlan 10,20",
        "nv set interface swp1 type swp",
        "nv set interface swp1 bridge domain br_default",
        "nv set interface swp1 bridge domain br_default access 20",
    ]


def test_NVUE_GETCONFIG_empty_manifest(applied_config):
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, {}, {})
    # every bridge vlan but the default goes, as do bonds and SVIs. Physical ports,
    # management and peerlink interfaces are left alone
    assert res == [
        "nv unset bridge domain br_default vlan 10,20,30,40-41",
        unsetInterfaces(),
    ]


def test_NVUE_GETCONFIG_managed(applied_config, vlans):
    interfaces = {
        "swp2": {"managed": True, "description": "ignored"},
        "bond3": {"managed": True},
        "vlan30": {"managed": True},
        "swp5s0": {"managed": True},
        "peerlink": {"managed": True},
        "lo": {"managed": True},
    }
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)
    # managed interfaces are not unset or set. bond1 and vlan10 are not in the
    # manifest, so they are removed
    assert res == [
        "nv unset bridge domain br_default vlan 30,41",
        "nv unset interface bond1,vlan10",
        "nv set bridge domain br_default vlan 10,20",
    ]


def test_NVUE_GETCONFIG_absent(applied_config, vlans):
    # the bonds and SVIs exist on the switch but not in the manifest so they are
    # removed, the physical ports that are not in it are left alone
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, {"swp2": {"state": "up"}}, vlans)
    assert res == [
        "nv unset bridge domain br_default vlan 30,41",
        "nv unset interface swp2,bond1,bond3,vlan10,vlan30",
        "nv set bridge domain br_default vlan 10,20",
        "nv set interface swp2 type swp",
        "nv set interface swp2 link state up",
    ]


@pytest.mark.parametrize(
    "intf_type, intf, removed",
    [
        ("sub", "swp1.100", True),
        ("tunnel", "tunnel1", True),
        ("bond", "uplink", True),
        ("svi", "vlan10", True),
        ("swp", "swp1", False),
        ("eth", "eth1", False),
        ("bridge", "br_default", False),
        ("vrf", "BLUE", False),
        ("loopback", "lo2", False),
    ],
)
def test_NVUE_GETCONFIG_absent_types(intf_type, intf, removed):
    applied = [{"set": {"interface": {intf: {"type": intf_type}}}}]
    res = nvidia_nvue.NVUE_GETCONFIG(applied, {}, {})
    assert res == ([f"nv unset interface {intf}"] if removed else [])


def test_NVUE_GETCONFIG_managed_subinterface():
    # a managed subinterface is kept, the driver can't configure it
    applied = [{"set": {"interface": {"swp1.100": {"type": "sub"}}}}]
    res = nvidia_nvue.NVUE_GETCONFIG(applied, {"swp1.100": {"managed": True}}, {})
    assert res == []


def test_NVUE_GETCONFIG_bond_members(applied_config, vlans):
    # every member of a rebuilt bond is reset too, a managed member is left alone
    interfaces = {
        "bond3": {"lacp-members-active": ["swp3", "swp4"]},
        "swp3": {},
        "swp4": {"managed": True},
    }
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)
    assert unsetInterfaces("bond3", "swp3") in res
    assert "nv set interface bond3 bond member swp3,swp4" in res
    assert "nv set interface swp3 type swp" in res


def test_NVUE_GETCONFIG_bond_member_missing(applied_config, vlans):
    interfaces = {"bond3": {"lacp-members-active": ["swp3", "swp4"]}, "swp3": {}}
    with pytest.raises(
        ValueError, match="bond member swp4 needs its own manifest entry"
    ):
        nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)


def test_NVUE_GETCONFIG_managed_bond_member(applied_config, vlans):
    # swp3 is a member of the managed bond3 on the switch, so it stays a member
    interfaces = {
        "bond3": {"managed": True},
        "swp3": {"portmode": "access", "untagged": 10},
    }
    with pytest.raises(ValueError, match="bond members"):
        nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)


def test_NVUE_GETCONFIG_removed_bond_member(applied_config, vlans):
    # bond3 is not in the manifest, so it is removed and swp3 is free
    interfaces = {"swp3": {"portmode": "access", "untagged": 10}}
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)
    assert unsetInterfaces("swp3") in res
    assert "nv set interface swp3 bridge domain br_default access 10" in res


def test_NVUE_GETCONFIG_combined_keys(combined_config):
    interfaces = {
        "bond1": {
            "description": "NERC-CORE uplink",
            "state": "up",
            "lacp-members-active": ["swp31", "swp32"],
            "lacp-rate": "fast",
            "mlag": 1,
            "portmode": "trunk",
            "tagged": "all",
            "stp": {"disabled": True},
        },
        "swp1": {"state": "up", "fec": "rs"},
        "swp31": {},
        "swp32": {},
    }
    vlans = {300: {"name": "TEST", "description": "Test"}}
    res = nvidia_nvue.NVUE_GETCONFIG(combined_config, interfaces, vlans)
    # swp1 only exists under combined keys and bond1 is spread over several, they are
    # still reset. bond11 and vlan2304 are not in the manifest, so they are removed
    assert "nv unset interface bond1,swp1,bond11,vlan2304" in res
    assert "nv set interface bond1 bridge domain br_default stp bpdu-filter on" in res


def test_NVUE_GETCONFIG_combined_bond_member(combined_config):
    # swp2 is a member of the managed bond11 on the switch, so it stays a member
    interfaces = {
        "bond11": {"managed": True},
        "swp2": {"portmode": "access", "untagged": 300},
    }
    vlans = {300: {"name": "TEST", "description": "Test"}}
    with pytest.raises(ValueError, match="bond members"):
        nvidia_nvue.NVUE_GETCONFIG(combined_config, interfaces, vlans)


def test_NVUE_GETCONFIG_fanout_children(applied_config, vlans):
    interfaces = {
        "swp5": {"fanout": {"type": "quad", "speed": "10G"}},
        "swp5s0": {"description": "child 0"},
        "swp5s1": {"managed": True},
        "swp5s2": None,
        "swp5s3": {"state": "up"},
    }
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)
    assert unsetInterfaces("swp5", "swp5s0", "swp5s2", "swp5s3") in res
    # the managed child gets no speed, the others do
    assert "nv set interface swp5s0,swp5s2,swp5s3 link speed 10G" in res
    assert "nv set interface swp5s0 description 'child 0'" in res


@pytest.mark.parametrize(
    "children, missing",
    [
        ({}, "swp5s0, swp5s1, swp5s2, swp5s3"),
        ({"swp5s0": {}, "swp5s1": {"managed": True}}, "swp5s2, swp5s3"),
    ],
)
def test_NVUE_GETCONFIG_fanout_missing_children(
    applied_config, vlans, children, missing
):
    interfaces = {"swp5": {"fanout": {"type": "quad", "speed": "10G"}}, **children}
    with pytest.raises(ValueError, match=f"manifest entries for {missing}$"):
        nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)


def test_NVUE_GETCONFIG_fanout_managed(applied_config, vlans):
    # the children of a managed parent would lose their lane speed
    interfaces = {
        "swp5": {"managed": True, "fanout": {"type": "dual", "speed": "50G"}},
        "swp5s0": {},
        "swp5s1": {},
    }
    with pytest.raises(ValueError, match="fanout parent port cannot be managed"):
        nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)


def test_NVUE_GETCONFIG_fanout_child_speed(applied_config, vlans):
    # repeating the fanout speed on a child port is fine
    interfaces = {
        "swp5": {"fanout": {"type": "dual", "speed": "50G"}},
        "swp5s0": {"speed": "50g"},
        "swp5s1": {},
    }
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)
    assert [cmd for cmd in res if "link speed" in cmd] == [
        "nv set interface swp5s0,swp5s1 link speed 50G"
    ]


@pytest.mark.parametrize(
    "build, expected",
    [
        (
            "Cumulus Linux 5.11.0",
            [
                "nv set interface swp5 link breakout 2x",
                "nv set interface swp5s0 link speed 50G",
                "nv set interface swp5s1 link speed 25G",
            ],
        ),
        # before 5.3 the lane speed is in the breakout mode
        (
            "Cumulus Linux 5.1.0",
            [
                "nv set interface swp5 link breakout 2x50G",
                "nv set interface swp5s1 link speed 25G",
            ],
        ),
    ],
)
def test_NVUE_GETCONFIG_fanout_child_speed_override(
    applied_config, vlans, build, expected
):
    # a child port's own speed wins over the fanout speed
    interfaces = {
        "swp5": {"fanout": {"type": "dual", "speed": "50G"}},
        "swp5s0": {},
        "swp5s1": {"speed": "25G"},
    }
    res = nvidia_nvue.NVUE_GETCONFIG(
        applied_config, interfaces, vlans, {"build": build}
    )
    assert [cmd for cmd in res if "link speed" in cmd or "breakout" in cmd] == expected


def test_NVUE_GETCONFIG_fanout_single(applied_config, vlans):
    # a single port fanout has no child ports to list
    interfaces = {"swp5": {"fanout": {"type": "single", "speed": "40G"}}}
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)
    assert "nv set interface swp5 link breakout 1x" in res
    assert "nv set interface swp5 link speed 40G" in res


def test_NVUE_GETCONFIG_fanout_removed(applied_config, vlans):
    # swp5 no longer breaks out, its child ports are physical ports that are not in
    # the manifest, so they stay
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, {"swp5": {"state": "up"}}, vlans)
    assert unsetInterfaces("swp5") in res
    assert not any("swp5s" in cmd for cmd in res)


def test_NVUE_GETCONFIG_fanout_51(applied_config, vlans):
    interfaces = {
        "swp5": {"fanout": {"type": "quad", "speed": "25G"}},
        "swp5s0": {},
        "swp5s1": {},
        "swp5s2": {},
        "swp5s3": {},
    }
    system = {"build": "Cumulus Linux 5.1.0"}
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans, system)
    # the child ports take their speed from the breakout mode
    assert unsetInterfaces("swp5", "swp5s0", "swp5s1", "swp5s2", "swp5s3") in res
    assert "nv set interface swp5 link breakout 4x25G" in res
    assert not any("link speed" in cmd for cmd in res)


@pytest.mark.parametrize("intf", ["eth0", "lo", "mgmt", "peerlink", "peerlink.4094"])
@pytest.mark.parametrize("managed", [True, False])
def test_NVUE_GETCONFIG_protected(applied_config, vlans, intf, managed):
    # management and peerlink interfaces are ignored, managed or not, and never
    # removed (the bonds and SVIs go because they are not in the manifest)
    interfaces = {intf: {"description": "x", "managed": managed}}
    res = nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)
    assert res == [
        "nv unset bridge domain br_default vlan 30,41",
        unsetInterfaces(),
        "nv set bridge domain br_default vlan 10,20",
    ]


def test_NVUE_GETCONFIG_protected_peerlink_members(applied_config, vlans):
    # peerlink is ignored, so its members on the switch stay bond members
    interfaces = {
        "peerlink": {"lacp-members-active": ["swp1"]},
        "swp49": {"portmode": "access", "untagged": 10},
    }
    with pytest.raises(ValueError, match="bond members"):
        nvidia_nvue.NVUE_GETCONFIG(applied_config, interfaces, vlans)


@pytest.mark.parametrize(
    "intf, error", [("Port-channel 10", "not a valid"), ("swp1-4", "ranges")]
)
def test_NVUE_GETCONFIG_invalid_name(applied_config, vlans, intf, error):
    with pytest.raises(ValueError, match=error):
        nvidia_nvue.NVUE_GETCONFIG(applied_config, {intf: {}}, vlans)


def test_NVUE_GETCONFIG_none_fields(vlans):
    # an interface with no fields is reset to defaults
    res = nvidia_nvue.NVUE_GETCONFIG([], {"swp1": None}, vlans)
    assert res[-1] == "nv set interface swp1 type swp"


#
# Switch prep
#


# fmt: off
@pytest.mark.parametrize(
    "build, auto_save",
    [
        # auto save arrived in 5.4 and switched to "state enabled" in 5.10
        ("Cumulus Linux 5.0.0",     []),
        ("Cumulus Linux 5.3.1",     []),
        ("Cumulus Linux 5.4.0",     ["nv set system config auto-save enable on"]),
        ("Cumulus Linux 5.9.2",     ["nv set system config auto-save enable on"]),
        ("Cumulus Linux 5.10.0",    ["nv set system config auto-save state enabled"]),
        ("Cumulus Linux 5.11.0",    ["nv set system config auto-save state enabled"]),
        ("Cumulus Linux 5.15.0",    ["nv set system config auto-save state enabled"]),
    ],
)
def test_NVUE_PREPCONFIG(applied_config, build, auto_save):
    res = nvidia_nvue.NVUE_PREPCONFIG(applied_config, "GPU-CORE-1", {"build": build})
    assert res == [
        "nv set system hostname GPU-CORE-1",
        "nv set bridge domain br_default",
    ] + auto_save
# fmt: on


def test_NVUE_PREPCONFIG_header_version(applied_config):
    # without "nv show system" the config header decides
    res = nvidia_nvue.NVUE_PREPCONFIG(
        withVersion(applied_config, "Cumulus Linux 5.9.1"), "GPU-CORE-1"
    )
    assert res[-1] == "nv set system config auto-save enable on"


def test_NVUE_PREPCONFIG_unknown_version():
    # without a release the driver assumes 5.11
    res = nvidia_nvue.NVUE_PREPCONFIG([], "GPU-CORE-1")
    assert res[-1] == "nv set system config auto-save state enabled"


def test_NVUE_PREPCONFIG_hostname_quoted():
    res = nvidia_nvue.NVUE_PREPCONFIG([], "leaf 1")
    assert res[0] == "nv set system hostname 'leaf 1'"


def test_FilterModule():
    filters = nvidia_nvue.FilterModule().filters()
    assert filters["NVUE_GETCONFIG"] is nvidia_nvue.NVUE_GETCONFIG
    assert filters["NVUE_PREPCONFIG"] is nvidia_nvue.NVUE_PREPCONFIG
