#!/usr/bin/env python3
"""
CI check script: reads the ansible inventory in `hosts` and verifies that no two
switches share the same ansible_host address.

Two entries with the same address would make ansible connect to the same switch
twice and push one switch's manifests onto the other. The switches share credentials
and host_key_checking is off, so nothing else would catch it.

Exits with status code 1 and prints the conflicting hosts if any are found,
otherwise exits 0.
"""

import ipaddress
import os
import shlex
import sys
from collections import defaultdict

HOSTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "hosts",
)


def parse_hosts(path):
    """
    Return a list of (lineno, hostname, ansible_host) for every host line in an
    INI inventory. ansible_host is None when the line doesn't set it.
    Comments, blank lines, and the [group:vars] / [group:children] sections are skipped.
    """
    hosts = []
    section_is_hosts = True
    with open(path, "r") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("["):
                section_is_hosts = ":" not in line
                continue
            if not section_is_hosts:
                continue
            fields = shlex.split(line, comments=True)
            hostname = fields[0]
            variables = dict(
                field.split("=", 1) for field in fields[1:] if "=" in field
            )
            hosts.append((lineno, hostname, variables.get("ansible_host")))
    return hosts


def normalize(address):
    """IPs are compared by value, anything else (a DNS name) case-insensitively."""
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return address.lower()


def find_duplicates(hosts):
    """
    Return {address: [(lineno, hostname), ...]} for every address used by more than
    one distinct hostname. The same host listed under several groups is allowed.
    """
    by_address = defaultdict(dict)
    for lineno, hostname, address in hosts:
        if address is None:
            continue
        by_address[normalize(address)].setdefault(hostname, lineno)
    return {
        address: sorted((lineno, name) for name, lineno in names.items())
        for address, names in by_address.items()
        if len(names) > 1
    }


def main():
    if not os.path.isfile(HOSTS_FILE):
        print(f"ERROR: hosts file not found at {HOSTS_FILE}")
        sys.exit(1)

    hosts = parse_hosts(HOSTS_FILE)
    duplicates = find_duplicates(hosts)

    if duplicates:
        print("ERROR: multiple hosts share the same ansible_host:")
        for address, entries in sorted(duplicates.items()):
            names = ", ".join(f"{name} (line {lineno})" for lineno, name in entries)
            print(f"  - {address}: {names}")
        sys.exit(1)

    print(f"OK: checked {len(hosts)} host(s), no duplicate ansible_host found.")
    sys.exit(0)


if __name__ == "__main__":
    main()
