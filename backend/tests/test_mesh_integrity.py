from pathlib import Path

import yaml

from scripts.check_mesh_integrity import exposed_services


def test_mesh_integrity_rejects_host_network_services():
    fixture = Path(__file__).parent / "fixtures" / "mesh-integrity-host-network.yml"
    compose = yaml.safe_load(fixture.read_text())

    violations = exposed_services(compose, "fixture")
    assert "fixture: service livekit uses host networking" in violations
    assert any("service livekit publishes" in violation for violation in violations)


def test_mesh_integrity_accepts_loopback_ports_on_bridge_network():
    compose = {
        "services": {
            "sfu": {
                "ports": [
                    "127.0.0.1:7880:7880/tcp",
                    "127.0.0.1:50000-51000:50000-51000/udp",
                ]
            }
        }
    }

    assert exposed_services(compose, "fixture") == []


def test_mesh_integrity_rejects_wildcard_port_publication():
    compose = {"services": {"db": {"ports": ["5432:5432"]}}}

    assert "127.0.0.1" in exposed_services(compose, "fixture")[0]
