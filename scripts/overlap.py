#!/usr/bin/env python3
"""
CI check script: reads subnets defined in group_vars/all/vlans.yaml
and verifies none of them overlap with each other.

Exits with status code 1 and prints overlapping pairs if any are found,
otherwise exits 0.
"""

import sys
import os
import ipaddress

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required to run this script (pip install pyyaml)")
    sys.exit(1)

VLANS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "group_vars",
    "all",
    "vlans.yaml",
)


def load_vlans(path):
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data or {}


def extract_subnets(data):
    """
    Extract (name, subnet_str) pairs from the vlans data structure.
    """
    subnets = []

    for network in data["vlans"]:
        name = network.get("name", "unknown")
        subnet = network.get("subnet4")
        if subnet:
            subnets.append((name, subnet))
    return subnets


def parse_networks(subnets):
    """
    Convert raw subnet strings into ipaddress network objects.
    Returns list of (name, network) and list of parse errors.
    """
    parsed = []
    errors = []
    for name, subnet_str in subnets:
        try:
            network = ipaddress.ip_network(subnet_str, strict=False)
            parsed.append((name, network))
        except ValueError as exc:
            errors.append((name, subnet_str, str(exc)))
    return parsed, errors


def find_overlaps(networks):
    """
    Given a list of (name, network) tuples, return a list of
    (name1, network1, name2, network2) tuples for each overlapping pair.
    """
    overlaps = []
    for i in range(len(networks)):
        name_a, net_a = networks[i]
        for j in range(i + 1, len(networks)):
            name_b, net_b = networks[j]
            if net_a.version != net_b.version:
                continue
            if net_a.overlaps(net_b):
                overlaps.append((name_a, net_a, name_b, net_b))
    return overlaps


def main():
    if not os.path.isfile(VLANS_FILE):
        print(f"ERROR: vlans file not found at {VLANS_FILE}")
        sys.exit(1)

    data = load_vlans(VLANS_FILE)
    raw_subnets = extract_subnets(data)

    if not raw_subnets:
        print(f"WARNING: no subnets found in {VLANS_FILE}")
        sys.exit(0)

    networks, parse_errors = parse_networks(raw_subnets)

    if parse_errors:
        print("ERROR: could not parse the following subnets:")
        for name, subnet_str, err in parse_errors:
            print(f"  - {name}: '{subnet_str}' ({err})")

    overlaps = find_overlaps(networks)

    if overlaps:
        print("ERROR: overlapping subnets detected:")
        for name_a, net_a, name_b, net_b in overlaps:
            print(f"  - {name_a} ({net_a}) overlaps with {name_b} ({net_b})")
        sys.exit(1)

    if parse_errors:
        sys.exit(1)

    print(f"OK: checked {len(networks)} subnet(s), no overlaps found.")
    sys.exit(0)


if __name__ == "__main__":
    main()
