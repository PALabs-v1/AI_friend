"""Fail when Compose publishes a service outside the loopback-only mesh."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml


def exposed_services(document: dict[str, Any], source: str = "compose") -> list[str]:
    violations = []
    services = document.get("services", {})
    if not isinstance(services, dict):
        return [f"{source}: services must be a mapping"]
    for name, service in services.items():
        if not isinstance(service, dict):
            violations.append(f"{source}: service {name} must be a mapping")
            continue
        if service.get("network_mode") == "host":
            violations.append(f"{source}: service {name} uses host networking")
        for port in service.get("ports", []) or []:
            host_ip = None
            if isinstance(port, dict):
                host_ip = port.get("host_ip")
            elif isinstance(port, str):
                parts = port.split(":")
                if len(parts) == 3:
                    host_ip = parts[0]
            if host_ip != "127.0.0.1":
                violations.append(
                    f"{source}: service {name} publishes a port without a "
                    "127.0.0.1 host binding"
                )
    return violations


def check_files(paths: list[Path]) -> list[str]:
    violations = []
    for path in paths:
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as error:
            violations.append(f"{path}: cannot parse Compose YAML: {error}")
            continue
        violations.extend(exposed_services(document, str(path)))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "compose",
        nargs="*",
        type=Path,
        default=[Path("docker-compose.infra.yml"), Path("docker-compose.prod.yml")],
    )
    args = parser.parse_args()
    violations = check_files(args.compose)
    if violations:
        print("\n".join(violations))
        return 1
    print("Compose services use loopback-only published ports and bridge networking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
