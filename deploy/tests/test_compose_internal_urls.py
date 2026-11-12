import re
from pathlib import Path

import yaml

COMPOSE = yaml.safe_load((Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8"))

# Plain http:// is how services talk to each other on the compose networks; external
# endpoints are https. A host in such a URL that is no service and no network alias
# resolves to nothing, and the caller only finds out on the first request. Address
# literals (the docker bridge gateway that reaches the GPU host) are not names.
INTERNAL_URL = re.compile(r"http://([A-Za-z][A-Za-z0-9._-]*)")


def _service_hosts() -> set[str]:
    hosts: set[str] = set()
    for name, service in COMPOSE["services"].items():
        hosts.add(name)
        if service.get("container_name"):
            hosts.add(service["container_name"])
        networks = service.get("networks")
        if isinstance(networks, dict):
            for config in networks.values():
                hosts.update((config or {}).get("aliases", []))
    return hosts


def test_plain_http_hosts_in_service_environment_name_a_compose_service():
    hosts = _service_hosts()
    unknown = []
    for name, service in COMPOSE["services"].items():
        environment = service.get("environment") or {}
        if isinstance(environment, list):
            environment = dict(entry.split("=", 1) for entry in environment if "=" in entry)
        for key, value in environment.items():
            for host in INTERNAL_URL.findall(str(value)):
                if host not in hosts:
                    unknown.append(f"{name}.{key}: {host}")
    assert not unknown, f"http:// URLs whose host is neither a compose service nor an alias: {unknown}"
