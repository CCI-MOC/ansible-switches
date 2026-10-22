import ipaddress
import re
import shlex

# Every switch has a single vlan-aware bridge with this name
BRIDGE = "br_default"

# NVUE syntax differs between Cumulus Linux 5 releases, the commands follow the
# release the switch runs. 5.3 added breakout modes without the lane speed (4x
# instead of 4x25G), 5.4 added auto save ("enable on"), 5.10 changed it to
# "state enabled", and 5.15 renamed the remaining on/off values to
# enabled/disabled and split "ip address/gateway" into "ipv4" and "ipv6"
BREAKOUT_LANES_VERSION = (5, 3)
AUTO_SAVE_VERSION = (5, 4)
AUTO_SAVE_STATE_VERSION = (5, 10)
ENABLED_DISABLED_VERSION = (5, 15)
IP_FAMILY_VERSION = (5, 15)

# Assumed when the release cannot be read
DEFAULT_VERSION = (5, 11)

# Breakout modes with the lane speed in them, which releases before 5.3 need.
# 5.2 added 2x10G and 2x25G
LANE_SPEED_BREAKOUTS = [
    "2x20G",
    "2x40G",
    "2x50G",
    "2x100G",
    "2x200G",
    "4x10G",
    "4x25G",
    "4x50G",
    "4x100G",
    "8x50G",
]
LANE_SPEED_BREAKOUTS_5_2 = ["2x10G", "2x25G"]
LANE_SPEED_BREAKOUTS_5_2_VERSION = (5, 2)

MTU_RANGE = (552, 9216)

# Resetting these would cut off management access or MLAG peering, so the
# driver never configures them, even when they are in the manifest
PROTECTED_INTERFACE_PATTERNS = [r"eth\d+", r"lo", r"mgmt", r"peerlink(\.\d+)?"]

# The interface types the driver can rebuild from manifest fields
REBUILT_INTERFACE_TYPES = ["swp", "bond", "svi"]

# Interfaces of these types are removed when they are not in the manifest.
# Physical ports (swp, eth) are left alone, and the rest (loopback, bridge, vrf
# and peerlink) belong to configuration this driver does not manage
REMOVABLE_INTERFACE_TYPES = ["bond", "svi", "sub", "tunnel"]

LAG_MEMBER_FIELDS = ["lag-members", "lacp-members-active"]

FEC_MODES = ["auto", "rs", "baser", "off"]

FANOUT_LANES = {"single": 1, "dual": 2, "quad": 4, "octal": 8}

# Manifest stp field -> NVUE stp option. NVUE has no per-port STP disable,
# filtering BPDUs is the equivalent (the NXOS driver does the same)
STP_OPTIONS = {
    "edgeport": "admin-edge",
    "bpduguard": "bpdu-guard",
    "rootguard": "restrrole",
    "disabled": "bpdu-filter",
}


def getAppliedDocs(applied):
    """
    Normalizes the parsed output of "nv config show -o json", which is a list of
    documents such as [{"header": {...}}, {"set": {...}}]

    :param applied: Parsed output of "nv config show -o json"
    :type applied: list
    :return: List of documents
    :rtype: list
    """
    if isinstance(applied, dict):
        applied = [applied]

    if not isinstance(applied, list):
        return []

    return [doc for doc in applied if isinstance(doc, dict)]


def getAppliedConfig(applied):
    """
    Returns the applied configuration tree from the output of "nv config show -o json"

    :param applied: Parsed output of "nv config show -o json"
    :type applied: list
    :return: The contents of the first "set" document
    :rtype: dict
    """
    for doc in getAppliedDocs(applied):
        if "set" in doc:
            return doc["set"] or {}

    return {}


def expandInterfaceKey(key):
    """
    NVUE combines interfaces that share settings into one key, for example
    "bond1,swp1-2,29-30" is bond1, swp1, swp2, swp29 and swp30. A part without a
    name ("29-30") continues the name of the part before it

    :param key: Interface key from the applied configuration
    :type key: str
    :return: List of interface names
    :rtype: list
    """
    intfs = []
    prefix = ""

    for part in str(key).split(","):
        match = re.fullmatch(r"(.*?)(\d+)(?:-(\d+))?", part)
        if match is None:
            # no number to expand, for example peerlink or lo
            intfs.append(part)
            continue

        if match.group(1):
            prefix = match.group(1)

        first = int(match.group(2))
        last = int(match.group(3)) if match.group(3) else first
        intfs += [f"{prefix}{i}" for i in range(first, last + 1)]

    return intfs


def mergeConfig(base, extra):
    """
    Merges two configuration trees, values from extra win

    :param base: First configuration tree
    :type base: dict
    :param extra: Second configuration tree
    :type extra: dict
    :return: Merged configuration tree
    :rtype: dict
    """
    merged = dict(base)

    for key, value in extra.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = mergeConfig(merged[key], value)
        else:
            merged[key] = value

    return merged


def getAppliedInterfaces(applied_cfg):
    """
    Returns the interface tree of the applied configuration with one entry per
    interface. NVUE shows settings that interfaces share once under a combined key,
    so the settings of each interface are merged from every key that includes it

    :param applied_cfg: Applied configuration tree
    :type applied_cfg: dict
    :return: Dict of interface name to its configuration
    :rtype: dict
    """
    out = {}

    for key, intf_cfg in (applied_cfg.get("interface") or {}).items():
        for intf in expandInterfaceKey(key):
            out[intf] = mergeConfig(out.get(intf, {}), intf_cfg or {})

    return out


def getNVUEVersion(applied, system=None):
    """
    Reads the Cumulus Linux release from the parsed output of "nv show system -o json",
    for example {"build": "Cumulus Linux 5.1.0"} returns (5, 1). Falls back to the
    header of "nv config show -o json", which releases before 5.11 may not print

    :param applied: Parsed output of "nv config show -o json"
    :type applied: list
    :param system: Parsed output of "nv show system -o json"
    :type system: dict
    :return: (major, minor), or None if neither has a version
    :rtype: tuple
    """
    versions = []
    if isinstance(system, dict):
        versions.append(system.get("build"))

    for doc in getAppliedDocs(applied):
        header = doc.get("header")
        if isinstance(header, dict):
            versions.append(header.get("version"))

    for version in versions:
        match = re.search(r"(\d+)\.(\d+)", str(version or ""))
        if match:
            return (int(match.group(1)), int(match.group(2)))

    return None


def releaseAtLeast(version, minimum):
    """
    :param version: Cumulus Linux release as (major, minor), None if unknown
    :type version: tuple
    :param minimum: Release that introduced a syntax
    :type minimum: tuple
    :return: True if the switch runs minimum or newer, judged by DEFAULT_VERSION if unknown
    :rtype: bool
    """
    return (version or DEFAULT_VERSION) >= minimum


def nvueBool(value, version):
    """
    Formats a boolean NVUE option for the running release

    :param value: Value of the option
    :type value: bool
    :param version: Cumulus Linux release as (major, minor)
    :type version: tuple
    :return: "on"/"off" before 5.15, "enabled"/"disabled" from 5.15
    :rtype: str
    """
    if releaseAtLeast(version, ENABLED_DISABLED_VERSION):
        return "enabled" if value else "disabled"

    return "on" if value else "off"


def nvueEnable(version, state_version):
    """
    Some NVUE features moved from "enable on" to "state enabled" in a later release

    :param version: Cumulus Linux release as (major, minor)
    :type version: tuple
    :param state_version: Release that switched the feature to "state enabled"
    :type state_version: tuple
    :return: "enable on" or "state enabled"
    :rtype: str
    """
    if releaseAtLeast(version, state_version):
        return "state enabled"

    return "enable on"


def getIPPath(ip_version, version):
    """
    :param ip_version: 4 or 6
    :type ip_version: int
    :param version: Cumulus Linux release as (major, minor)
    :type version: tuple
    :return: The NVUE path for addresses and gateways, "ip" before 5.15, "ipv4" or
        "ipv6" from 5.15
    :rtype: str
    """
    if releaseAtLeast(version, IP_FAMILY_VERSION):
        return f"ipv{ip_version}"

    return "ip"


def getInt(intf, field, value, minimum, maximum):
    """
    Reads a whole number manifest field, booleans and other types are rejected

    :param intf: Name of the interface
    :type intf: str
    :param field: Name of the manifest field
    :type field: str
    :param value: Value of the field
    :type value: int
    :param minimum: Lowest allowed value
    :type minimum: int
    :param maximum: Highest allowed value
    :type maximum: int
    :return: The value
    :rtype: int
    """
    is_number = isinstance(value, (int, str)) and not isinstance(value, bool)
    if (
        not is_number
        or not str(value).isdigit()
        or not minimum <= int(value) <= maximum
    ):
        raise ValueError(
            f"{intf}: {field} must be a number from {minimum} to {maximum}, not {value}"
        )

    return int(value)


def getIPAddress(intf, field, value, ip_version=None):
    """
    Reads an address manifest field. Interface addresses (ip_version set) need their
    prefix length, a bare address or a netmask is not accepted. Gateways (ip_version
    None) are an address of either family

    :param intf: Name of the interface
    :type intf: str
    :param field: Name of the manifest field
    :type field: str
    :param value: The address, for example 10.0.0.1/24
    :type value: str
    :param ip_version: 4 or 6 for an interface address, None for a gateway
    :type ip_version: int
    :return: The parsed address
    :rtype: ipaddress.IPv4Interface, IPv6Interface, IPv4Address or IPv6Address
    """
    try:
        if ip_version is None:
            return ipaddress.ip_address(value)

        address = ipaddress.ip_interface(value)
    except ValueError:
        address = None

    if ip_version is None:
        raise ValueError(f"{intf}: {field} {value} is not a valid IP address")

    has_prefix = re.fullmatch(r"[^/]+/\d+", value) is not None
    if address is None or not has_prefix or address.version != ip_version:
        raise ValueError(
            f"{intf}: {field} must be an IPv{ip_version} address with a prefix length, not {value}"
        )

    return address


def parseVLANList(vlan_keys):
    """
    Expands NVUE vlan keys into vlan IDs. NVUE compresses vlan lists, so a single
    key can be a vlan ("10"), a list ("10,20") or a range ("30-32")

    :param vlan_keys: NVUE vlan keys
    :type vlan_keys: iterable
    :return: Set of vlan IDs
    :rtype: set
    """
    vlan_ids = set()

    for key in vlan_keys:
        for part in str(key).split(","):
            part = part.strip()
            if "-" in part:
                vlan_first, vlan_last = part.split("-")
                vlan_ids.update(range(int(vlan_first), int(vlan_last) + 1))
            elif part.isdigit():
                vlan_ids.add(int(part))

    return vlan_ids


def compressVLANList(vlan_ids):
    """
    Formats vlan IDs as an NVUE vlan list. For example [10, 11, 12, 20] becomes "10-12,20"

    :param vlan_ids: vlan IDs
    :type vlan_ids: iterable
    :return: NVUE vlan list
    :rtype: str
    """
    sorted_ids = sorted(set(vlan_ids))
    parts = []

    i = 0
    while i < len(sorted_ids):
        j = i
        while j + 1 < len(sorted_ids) and sorted_ids[j + 1] == sorted_ids[j] + 1:
            j += 1

        if j > i:
            parts.append(f"{sorted_ids[i]}-{sorted_ids[j]}")
        else:
            parts.append(str(sorted_ids[i]))

        i = j + 1

    return ",".join(parts)


def getTaggedVLANs(intf, tagged):
    """
    The manifest allows tagged vlans as a list of IDs and X:Y ranges. This method
    parses that ("all" is handled by the caller)

    :param intf: Name of the interface
    :type intf: str
    :param tagged: The "tagged" manifest field
    :type tagged: list
    :return: Set of vlan IDs
    :rtype: set
    """
    if not isinstance(tagged, list):
        tagged = [tagged]

    vlan_ids = set()
    for item in tagged:
        item_parts = item.split(":") if isinstance(item, str) else [item]
        if len(item_parts) > 2:
            raise ValueError(f"{intf}: tagged item {item} is not a vlan or X:Y range")

        bounds = [getInt(intf, "tagged", part, 1, 4094) for part in item_parts]
        vlan_ids.update(range(bounds[0], bounds[-1] + 1))

    return vlan_ids


def getMLAGID(intf, mlag):
    """
    NVUE identifies an MLAG bond by an ID that must match on both peers. The manifest
    holds the label of the peer bond, so the ID is its trailing number (bond10 -> 10).
    An integer is used as the ID directly

    :param intf: Name of the bond
    :type intf: str
    :param mlag: The "mlag" manifest field
    :type mlag: str
    :return: MLAG ID
    :rtype: int
    """
    if isinstance(mlag, int) and not isinstance(mlag, bool):
        mlag_id = mlag
    else:
        match = re.search(r"(\d+)\s*$", str(mlag))
        if match is None:
            raise ValueError(
                f"{intf}: cannot derive an MLAG ID from mlag '{mlag}', use a number"
            )
        mlag_id = int(match.group(1))

    if not 1 <= mlag_id <= 65535:
        raise ValueError(f"{intf}: MLAG ID {mlag_id} is outside 1-65535")

    return mlag_id


def hasMLAG(fields):
    """
    :param fields: Manifest fields of the interface
    :type fields: dict
    :return: True if the "mlag" field is set, 0 counts as set so that it is rejected
    :rtype: bool
    """
    mlag = fields.get("mlag")
    return mlag is not None and mlag is not False and mlag != ""


def checkInterfaceName(intf):
    """
    Names end up in shell commands and NVUE interface lists, so only allow the
    characters Cumulus uses (swp1s0, bond1, vlan10, ...). NVUE reads swp1-4 as a
    range of interfaces, so ranges are not allowed either

    :param intf: Name of the interface
    :type intf: str
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(intf)):
        raise ValueError(f"{intf}: not a valid Cumulus Linux interface name")

    if re.search(r"\d-\d", str(intf)):
        raise ValueError(
            f"{intf}: interface ranges are not allowed, list each interface"
        )


def getInterfaceType(intf, fields, applied_interfaces):
    """
    Determines the NVUE type of a manifest interface

    :param intf: Name of the interface
    :type intf: str
    :param fields: Manifest fields of the interface
    :type fields: dict
    :param applied_interfaces: Interface tree of the applied configuration
    :type applied_interfaces: dict
    :return: "svi", "bond" or "swp"
    :rtype: str
    """
    if re.fullmatch(r"vlan\d+", intf):
        return "svi"

    if any(field in fields for field in LAG_MEMBER_FIELDS) or intf.startswith("bond"):
        return "bond"

    if applied_interfaces.get(intf, {}).get("type") == "bond":
        return "bond"

    return "swp"


def getSpeed(intf, speed):
    """
    :param intf: Name of the interface
    :type intf: str
    :param speed: A "speed" manifest field, for example 25G or auto
    :type speed: str
    :return: The speed as NVUE writes it (25G, 100M or auto)
    :rtype: str
    """
    speed = str(speed)
    if speed.lower() == "auto":
        return "auto"

    if not re.fullmatch(r"\d+[MmGg]", speed):
        raise ValueError(f"{intf}: speed must be auto or a speed like 25G, not {speed}")

    return speed.upper()


def getFanoutLanes(intf, fanout):
    """
    :param intf: Name of the parent port
    :type intf: str
    :param fanout: The "fanout" manifest field
    :type fanout: dict
    :return: Number of ports the parent breaks out into
    :rtype: int
    """
    fanout_type = fanout.get("type", fanout.get("mode"))
    if fanout_type not in FANOUT_LANES:
        raise ValueError(
            f"{intf}: fanout type must be one of {', '.join(FANOUT_LANES)}, not {fanout_type}"
        )

    return FANOUT_LANES[fanout_type]


def getFanoutChildren(intf, lanes):
    """
    :param intf: Name of the parent port
    :type intf: str
    :param lanes: Number of ports the parent breaks out into
    :type lanes: int
    :return: Names of the child ports (swp1s0, swp1s1, ...)
    :rtype: list
    """
    if lanes == 1:
        return []

    return [f"{intf}s{i}" for i in range(lanes)]


def getFanoutParent(intf):
    """
    :param intf: Name of the interface
    :type intf: str
    :return: The parent port of a breakout child port (swp1 for swp1s0), else None
    :rtype: str
    """
    match = re.fullmatch(r"(swp\d+)s\d+", intf)
    return match.group(1) if match else None


def isProtected(intf):
    """
    :param intf: Name of the interface
    :type intf: str
    :return: True for management (eth, lo, mgmt) and peerlink interfaces, which are
        never configured
    :rtype: bool
    """
    return any(re.fullmatch(pattern, intf) for pattern in PROTECTED_INTERFACE_PATTERNS)


def checkManifest(interfaces, applied_interfaces):
    """
    Rejects manifest entries the driver would turn into wrong commands: interfaces it
    cannot rebuild, inconsistent breakouts, and bond members without a manifest entry

    :param interfaces: The interfaces manifest, without protected interfaces
    :type interfaces: dict
    :param applied_interfaces: Interface tree of the applied configuration
    :type applied_interfaces: dict
    """
    for intf, fields in interfaces.items():
        if fields.get("managed"):
            # the child ports need the breakout and lane speed from the fanout
            if "fanout" in fields:
                raise ValueError(f"{intf}: a fanout parent port cannot be managed")
            continue

        # Subinterfaces, tunnels and the other interface types have no manifest
        # fields, so they can only be kept with managed: true
        intf_type = getInterfaceType(intf, fields, applied_interfaces)
        applied_type = applied_interfaces.get(intf, {}).get("type")
        is_port = re.fullmatch(r"swp\d+(s\d+)?", intf)
        if applied_type not in REBUILT_INTERFACE_TYPES + [None] or (
            intf_type == "swp" and not is_port
        ):
            raise ValueError(
                f"{intf}: the driver only configures swp ports, bonds and SVIs, list it with managed: true"
            )

        # A fanout creates its child ports, which all need a manifest entry
        if "fanout" in fields:
            lanes = getFanoutLanes(intf, fields["fanout"])
            missing = [
                child
                for child in getFanoutChildren(intf, lanes)
                if child not in interfaces
            ]
            if missing:
                raise ValueError(
                    f"{intf}: the fanout needs manifest entries for {', '.join(missing)}"
                )

        # A child port needs the fanout of its parent, which sets the breakout and
        # the lane speed
        parent = getFanoutParent(intf)
        if parent is not None:
            parent_fanout = interfaces.get(parent, {}).get("fanout")
            if parent_fanout is None:
                raise ValueError(
                    f"{intf}: breakout port needs its parent {parent} in the manifest with a fanout"
                )
            if intf not in getFanoutChildren(
                parent, getFanoutLanes(parent, parent_fanout)
            ):
                raise ValueError(
                    f"{intf}: the {parent} fanout does not create this port"
                )

        # Bond members need their own entry so that their old config is reset too
        if intf_type == "bond":
            for member in [m for f in LAG_MEMBER_FIELDS for m in fields.get(f) or []]:
                checkInterfaceName(member)
                if isProtected(member):
                    raise ValueError(f"{intf}: {member} cannot be a bond member")
                if member not in interfaces:
                    raise ValueError(
                        f"{intf}: bond member {member} needs its own manifest entry"
                    )

                member_fanout = interfaces[member].get("fanout")
                if member_fanout and getFanoutLanes(member, member_fanout) > 1:
                    raise ValueError(
                        f"{intf}: bond member {member} is broken out, use its child ports"
                    )


def getBondMembers(interfaces, applied_interfaces):
    """
    Returns the members of every bond that exists after this run. Bonds rebuilt from
    the manifest use their manifest members, managed bonds and the MLAG peerlink keep
    their applied members, and bonds that are not in the manifest are removed

    :param interfaces: The interfaces manifest
    :type interfaces: dict
    :param applied_interfaces: Interface tree of the applied configuration
    :type applied_interfaces: dict
    :return: Set of member interfaces
    :rtype: set
    """
    members = set()

    for fields in interfaces.values():
        if fields.get("managed"):
            continue

        for field in LAG_MEMBER_FIELDS:
            members.update(fields.get(field) or [])

    for intf, intf_cfg in applied_interfaces.items():
        if isProtected(intf) or interfaces.get(intf, {}).get("managed"):
            for member_key in (intf_cfg.get("bond") or {}).get("member") or {}:
                members.update(expandInterfaceKey(member_key))

    return members


def generateFanoutCommands(intf, fanout, lanes, version=None):
    """
    Generates the NVUE commands for a breakout port. The parent port gets the breakout
    mode, and the child ports (or the parent for "single") get the lane speed. Before
    5.3 the lane speed is part of the breakout mode instead (4x25G)

    :param intf: Name of the parent port
    :type intf: str
    :param fanout: The "fanout" manifest field
    :type fanout: dict
    :param lanes: Number of ports the parent breaks out into
    :type lanes: int
    :param version: Cumulus Linux release as (major, minor)
    :type version: tuple
    :return: Dict of interface name to command suffixes
    :rtype: dict
    """
    speed = getSpeed(intf, fanout["speed"]) if fanout.get("speed") else None

    if lanes > 1 and not releaseAtLeast(version, BREAKOUT_LANES_VERSION):
        if not speed:
            raise ValueError(f"{intf}: fanout needs a speed before Cumulus Linux 5.3")

        mode = f"{lanes}x{speed}"
        modes = list(LANE_SPEED_BREAKOUTS)
        if releaseAtLeast(version, LANE_SPEED_BREAKOUTS_5_2_VERSION):
            modes += LANE_SPEED_BREAKOUTS_5_2
        if mode not in modes:
            raise ValueError(
                f"{intf}: Cumulus Linux {version[0]}.{version[1]} has no {mode} breakout, "
                f"it supports {', '.join(modes)}"
            )

        return {intf: ["type swp", f"link breakout {mode}"]}

    out = {intf: ["type swp", f"link breakout {lanes}x"]}

    if speed:
        speed_cmd = f"link speed {speed}"
        for speed_intf in getFanoutChildren(intf, lanes) or [intf]:
            out.setdefault(speed_intf, ["type swp"]).append(speed_cmd)

    return out


def generateINTFCommands(intf, fields, intf_type, bond_members, bridge_vlans, version):
    """
    Generates the NVUE commands for a single interface from its manifest fields.
    The interface is unset before these run, so anything the manifest leaves out
    returns to the NVUE default

    :param intf: Name of the interface
    :type intf: str
    :param fields: Manifest fields of the interface
    :type fields: dict
    :param intf_type: NVUE type of the interface ("swp", "bond" or "svi")
    :type intf_type: str
    :param bond_members: Interfaces that are members of a bond
    :type bond_members: set
    :param bridge_vlans: vlans on the bridge after this run
    :type bridge_vlans: set
    :param version: Cumulus Linux release as (major, minor)
    :type version: tuple
    :return: List of command suffixes that follow "nv set interface <intf>"
    :rtype: list
    """

    def checkVLANs(vlan_ids):
        # Ports can only use vlans on the bridge, vlan 1 is the bridge default
        missing = set(vlan_ids) - bridge_vlans - {1}
        if missing:
            raise ValueError(
                f"{intf}: vlans {compressVLANList(missing)} are not in the vlan manifest of this switch"
            )

    # Cumulus Linux only runs LACP in active mode
    if "lacp-members-passive" in fields:
        raise ValueError(
            f"{intf}: lacp-members-passive is not supported, Cumulus Linux only runs active LACP"
        )

    out = [f"type {intf_type}"]

    if intf_type == "svi":
        vlan_id = int(intf.removeprefix("vlan"))
        checkVLANs([vlan_id])
        out.append(f"vlan {vlan_id}")

    # "description" field, null leaves the interface without a description
    if fields.get("description") is not None:
        out.append(f"description {shlex.quote(str(fields['description']))}")

    # "state" field
    if "state" in fields:
        if fields["state"] not in ("up", "down"):
            raise ValueError(f"{intf}: state must be up or down, not {fields['state']}")
        out.append(f"link state {fields['state']}")

    # "mtu" field, bond members inherit the mtu of their bond
    if "mtu" in fields:
        out.append(f"link mtu {getInt(intf, 'mtu', fields['mtu'], *MTU_RANGE)}")

    if intf_type == "swp":
        # "autoneg" field
        if "autoneg" in fields:
            out.append(f"link auto-negotiate {nvueBool(fields['autoneg'], version)}")

        # "speed" field, NVUE turns auto-negotiation off when a speed is set
        if "speed" in fields:
            out.append(f"link speed {getSpeed(intf, fields['speed'])}")

        # "fec" field, false turns FEC off, otherwise the value is the NVUE FEC mode
        if "fec" in fields:
            fec = "off" if fields["fec"] is False else str(fields["fec"]).lower()
            if fec not in FEC_MODES:
                raise ValueError(
                    f"{intf}: fec must be false or one of {', '.join(FEC_MODES)}, not {fields['fec']}"
                )
            out.append(f"link fec {fec}")

    if intf_type == "bond":
        # "lag-members" and "lacp-members-active" fields
        static_members = fields.get("lag-members") or []
        lacp_members = fields.get("lacp-members-active") or []
        if static_members and lacp_members:
            raise ValueError(f"{intf}: a bond cannot mix lag-members and LACP members")

        members = static_members + lacp_members
        if members:
            out.append(f"bond member {','.join(members)}")

        is_lacp = not static_members
        out.append(f"bond mode {'lacp' if is_lacp else 'static'}")

        # "lacp-rate" field
        if "lacp-rate" in fields:
            if fields["lacp-rate"] not in ("fast", "slow"):
                raise ValueError(
                    f"{intf}: lacp-rate must be fast or slow, not {fields['lacp-rate']}"
                )
            if is_lacp:
                out.append(f"bond lacp-rate {fields['lacp-rate']}")

        # "lacp-bypass" field, lets the bond forward before LACP comes up (PXE boot)
        if fields.get("lacp-bypass") and is_lacp:
            out.append(f"bond lacp-bypass {nvueBool(True, version)}")

        # "mlag" field, NVUE stores the enable flag next to the ID so set both
        if hasMLAG(fields):
            out.append(f"bond mlag id {getMLAGID(intf, fields['mlag'])}")
            out.append(f"bond mlag {nvueEnable(version, ENABLED_DISABLED_VERSION)}")
    elif hasMLAG(fields):
        raise ValueError(f"{intf}: mlag is only supported on bonds")

    # "portmode", "untagged" and "tagged" fields
    portmode = fields.get("portmode")
    untagged = None
    if "untagged" in fields:
        untagged = getInt(intf, "untagged", fields["untagged"], 1, 4094)

    if portmode is None:
        if "tagged" in fields or untagged is not None:
            raise ValueError(f"{intf}: tagged and untagged require a portmode")
    else:
        if portmode not in ("access", "trunk", "hybrid"):
            raise ValueError(
                f"{intf}: portmode must be access, trunk or hybrid, not {portmode}"
            )
        if intf_type == "svi" or intf in bond_members:
            raise ValueError(
                f"{intf}: portmode is not supported on SVIs or bond members"
            )
        if any(field in fields for field in ("ip4", "ip6", "gateway")):
            raise ValueError(
                f"{intf}: an L2 port (portmode) cannot have an IP address or gateway"
            )

        domain = f"bridge domain {BRIDGE}"
        out.append(domain)

        if portmode == "access":
            if "tagged" in fields:
                raise ValueError(f"{intf}: access ports cannot have tagged vlans")

            access_vlan = untagged if untagged is not None else 1
            checkVLANs([access_vlan])
            out.append(f"{domain} access {access_vlan}")
        else:
            if portmode == "trunk" and untagged is not None:
                raise ValueError(
                    f"{intf}: trunk ports cannot have an untagged vlan, use hybrid"
                )

            # "all" carries every bridge vlan, which is what NVUE does when the port
            # has no vlan list. Otherwise, like OS9, a port without tagged vlans
            # carries none and only its untagged vlan is left
            if fields.get("tagged") != "all":
                vlan_ids = (
                    getTaggedVLANs(intf, fields["tagged"])
                    if "tagged" in fields
                    else set()
                )
                if untagged is not None:
                    vlan_ids.add(untagged)
                if not vlan_ids:
                    raise ValueError(
                        f'{intf}: the port carries no vlans, set tagged (or tagged: "all") or untagged'
                    )

                checkVLANs(vlan_ids)
                out.append(f"{domain} vlan {compressVLANList(vlan_ids)}")
            elif untagged is not None:
                checkVLANs([untagged])

            if untagged is not None:
                out.append(f"{domain} untagged {untagged}")
            else:
                # trunk ports drop untagged frames instead of using the bridge pvid
                out.append(f"{domain} untagged none")

        # "stp" field, only applies to bridge ports
        stp_fields = fields.get("stp") or {}
        if not isinstance(stp_fields, dict):
            raise ValueError(f"{intf}: stp must be a mapping of stp options")
        for stp_field, stp_option in STP_OPTIONS.items():
            if stp_fields.get(stp_field):
                out.append(f"{domain} stp {stp_option} {nvueBool(True, version)}")

    # "ip4", "ip6" and "gateway" fields
    for ip_field, ip_version in (("ip4", 4), ("ip6", 6), ("gateway", None)):
        values = fields.get(ip_field, [])
        if not isinstance(values, list):
            values = [values]

        command = "address" if ip_version else "gateway"
        for value in values:
            address = getIPAddress(intf, ip_field, str(value), ip_version)
            out.append(f"{getIPPath(address.version, version)} {command} {value}")

    return out


def groupCommands(intf_cmds):
    """
    NVUE takes a list of interfaces in one command and every nv command is slow, so
    interfaces that share a setting are combined, for example
    "nv set interface swp1,swp2 link mtu 9216". The types go first, so that every
    interface exists with its type before anything else is set on it

    :param intf_cmds: Dict of interface name to command suffixes
    :type intf_cmds: dict
    :return: List of nv commands
    :rtype: list
    """
    groups = {}
    for intf, suffixes in intf_cmds.items():
        for suffix in suffixes:
            groups.setdefault(suffix, []).append(intf)

    ordered = sorted(groups.items(), key=lambda group: not group[0].startswith("type "))

    return [f"nv set interface {','.join(intfs)} {suffix}" for suffix, intfs in ordered]


def NVUE_PREPCONFIG(applied, hostname, system=None):
    """
    Returns the NVUE commands that prepare the switch on every run: the hostname, the
    br_default bridge, and saving to the startup configuration on apply. Releases
    before 5.4 have no auto save, there the "nv config save" after apply is the only
    save

    :param applied: Parsed output of "nv config show -o json"
    :type applied: list
    :param hostname: Hostname of the switch
    :type hostname: str
    :param system: Parsed output of "nv show system -o json"
    :type system: dict
    :return: List of nv commands
    :rtype: list
    """
    version = getNVUEVersion(applied, system)

    out = [
        f"nv set system hostname {shlex.quote(str(hostname))}",
        f"nv set bridge domain {BRIDGE}",
    ]

    if releaseAtLeast(version, AUTO_SAVE_VERSION):
        out.append(
            f"nv set system config auto-save {nvueEnable(version, AUTO_SAVE_STATE_VERSION)}"
        )

    return out


def NVUE_GETCONFIG(applied, interfaces, vlans, system=None):
    """
    This is the main method that generates the NVUE commands which make the switch
    match the manifest. Every unmanaged interface in the manifest is unset and rebuilt
    from its manifest fields. Physical ports that are not in the manifest are left
    alone, while bonds, SVIs, subinterfaces and tunnels that are not in it are removed.
    Bridge vlans that are not in the vlan manifest are removed (managed vlans are
    kept if they exist). NVUE stages everything in one pending revision and
    applies only the difference, so unchanged interfaces are not touched

    :param applied: Parsed output of "nv config show -o json"
    :type applied: list
    :param interfaces: The interfaces manifest
    :type interfaces: dict
    :param vlans: The VLANs manifest
    :type vlans: dict
    :param system: Parsed output of "nv show system -o json"
    :type system: dict
    :return: List of nv commands
    :rtype: list
    """
    applied_cfg = getAppliedConfig(applied)
    version = getNVUEVersion(applied, system)
    applied_interfaces = getAppliedInterfaces(applied_cfg)
    interfaces = {
        str(intf): fields or {} for intf, fields in (interfaces or {}).items()
    }
    vlans = vlans or {}

    for intf in interfaces:
        checkInterfaceName(intf)

    # Drop management and peerlink interfaces, the rest of the driver then treats
    # them like any interface that is not in the manifest
    interfaces = {
        intf: fields for intf, fields in interfaces.items() if not isProtected(intf)
    }
    checkManifest(interfaces, applied_interfaces)

    # Bridge vlans
    applied_bridge = ((applied_cfg.get("bridge") or {}).get("domain") or {}).get(
        BRIDGE
    ) or {}
    existing_vlans = parseVLANList((applied_bridge.get("vlan") or {}).keys())
    unmanaged_vlans = {
        int(vlan) for vlan, fields in vlans.items() if not (fields or {}).get("managed")
    }
    managed_vlans = {
        int(vlan) for vlan, fields in vlans.items() if (fields or {}).get("managed")
    }
    bridge_vlans = unmanaged_vlans | (managed_vlans & existing_vlans)
    stale_vlans = existing_vlans - bridge_vlans - {1}  # vlan 1 is the bridge default

    # Interfaces
    bond_members = getBondMembers(interfaces, applied_interfaces)
    intf_cmds = {}

    def addCommands(intf, suffixes):
        cur_cmds = intf_cmds.setdefault(intf, [])
        cur_cmds += [suffix for suffix in suffixes if suffix not in cur_cmds]

    for intf, fields in interfaces.items():
        if fields.get("managed"):
            continue

        # "fanout" field, a breakout port has no other configuration
        if "fanout" in fields:
            lanes = getFanoutLanes(intf, fields["fanout"])
            for fanout_intf, suffixes in generateFanoutCommands(
                intf, fields["fanout"], lanes, version
            ).items():
                port_fields = interfaces[fanout_intf]
                if port_fields.get("managed"):
                    continue

                # a child port's own speed wins over the fanout speed, for example a
                # 10G port on a 4x25G breakout
                if fanout_intf != intf and "speed" in port_fields:
                    suffixes = [s for s in suffixes if not s.startswith("link speed ")]

                addCommands(fanout_intf, suffixes)
            continue

        intf_type = getInterfaceType(intf, fields, applied_interfaces)
        addCommands(
            intf,
            generateINTFCommands(
                intf,
                fields,
                intf_type,
                bond_members,
                bridge_vlans,
                version,
            ),
        )

    out = []

    if stale_vlans:
        out.append(
            f"nv unset bridge domain {BRIDGE} vlan {compressVLANList(stale_vlans)}"
        )

    # Unmanaged manifest interfaces are rebuilt from scratch. Of the interfaces that
    # are not in the manifest, bonds, SVIs, subinterfaces and tunnels are removed
    # and physical ports are left alone
    unset_intfs = [
        intf
        for intf, fields in interfaces.items()
        if not fields.get("managed") and intf in applied_interfaces
    ]
    unset_intfs += [
        intf
        for intf, intf_cfg in applied_interfaces.items()
        if intf not in interfaces
        and not isProtected(intf)
        and intf_cfg.get("type") in REMOVABLE_INTERFACE_TYPES
    ]
    if unset_intfs:
        out.append(f"nv unset interface {','.join(unset_intfs)}")

    if unmanaged_vlans:
        out.append(
            f"nv set bridge domain {BRIDGE} vlan {compressVLANList(unmanaged_vlans)}"
        )

    out += groupCommands(intf_cmds)

    return out


class FilterModule(object):
    def filters(self):
        return {
            "NVUE_GETCONFIG": NVUE_GETCONFIG,
            "NVUE_PREPCONFIG": NVUE_PREPCONFIG,
        }
