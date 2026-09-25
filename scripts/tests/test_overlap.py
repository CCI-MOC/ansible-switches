import sys
import os
import ipaddress
from unittest.mock import patch, mock_open

# Ensure the scripts directory is in the path to import overlap.py
sys.path.insert(0, os.path.abspath(os.path.dirname(os.path.dirname(__file__))))
import overlap


def test_load_vlans_valid():
    yaml_content = """
    vlans:
      - name: mgmt
        subnet4: 10.0.0.0/24
    """
    with patch("builtins.open", mock_open(read_data=yaml_content)):
        data = overlap.load_vlans("dummy/path.yaml")
        assert "vlans" in data
        assert data["vlans"][0]["name"] == "mgmt"


def test_extract_subnets():
    data = {
        "vlans": [
            {"name": "vlan-10", "subnet4": "192.168.10.0/24"},
            {"name": "vlan-20"},  # Missing subnet4
            {"subnet4": "192.168.30.0/24"},  # Missing name
        ]
    }
    subnets = overlap.extract_subnets(data)
    assert len(subnets) == 2
    assert subnets[0] == ("vlan-10", "192.168.10.0/24")
    assert subnets[1] == ("unknown", "192.168.30.0/24")


def test_parse_networks_success():
    raw = [("mgmt", "10.0.0.0/24"), ("storage", "192.168.1.128/25")]
    parsed, errors = overlap.parse_networks(raw)
    assert len(errors) == 0
    assert len(parsed) == 2
    assert parsed[0][0] == "mgmt"
    assert isinstance(parsed[0][1], ipaddress.IPv4Network)


def test_parse_networks_with_errors():
    raw = [
        ("valid", "10.0.0.0/24"),
        ("invalid", "10.0.0.999/24"),
        ("garbage", "not-an-ip"),
    ]
    parsed, errors = overlap.parse_networks(raw)
    assert len(parsed) == 1
    assert len(errors) == 2

    # Check that error details were captured
    assert errors[0][0] == "invalid"
    assert errors[1][0] == "garbage"
    assert "not-an-ip" in errors[1][1]


def test_find_overlaps_no_overlap():
    networks = [
        ("netA", ipaddress.ip_network("10.0.0.0/24")),
        ("netB", ipaddress.ip_network("10.0.1.0/24")),
    ]
    overlaps = overlap.find_overlaps(networks)
    assert len(overlaps) == 0


def test_find_overlaps_detects_overlap():
    networks = [
        ("large_net", ipaddress.ip_network("10.0.0.0/16")),
        ("small_net", ipaddress.ip_network("10.0.50.0/24")),
        ("unrelated", ipaddress.ip_network("192.168.1.0/24")),
    ]
    overlaps = overlap.find_overlaps(networks)
    assert len(overlaps) == 1

    name_a, net_a, name_b, net_b = overlaps[0]
    assert name_a == "large_net"
    assert name_b == "small_net"
