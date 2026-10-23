from __future__ import annotations

import os
import re
import base64
import json
import ipaddress
import subprocess
from functools import lru_cache
from pathlib import Path

from openstack.connection import Connection
from openstack.exceptions import ConflictException, NotFoundException
from .software import SOFTWARE, minimum_ram, software_files, validate_software

AUTH_URL = os.getenv("OS_AUTH_URL", "")
ADMIN_USER = os.getenv("OS_USERNAME", "")
ADMIN_PASSWORD = os.getenv("OS_PASSWORD", "")
SERVICE_USER = os.getenv("OS_SERVICE_USERNAME", "portal-service")
SERVICE_PASSWORD = os.getenv("OS_SERVICE_PASSWORD", "")
DOMAIN = os.getenv("OS_USER_DOMAIN_NAME", "Default")
PROJECT_DOMAIN = os.getenv("OS_PROJECT_DOMAIN_NAME", "Default")
REGION = os.getenv("OS_REGION_NAME", "RegionOne")
INTERFACE = os.getenv("OS_INTERFACE", "public")
VERIFY = os.getenv("OS_VERIFY", "true").lower() in {"1", "true", "yes"}
SHARED_ROUTER = os.getenv("PORTAL_SHARED_ROUTER", "router1")
DNS_SERVERS = [x.strip() for x in os.getenv("PORTAL_DNS_SERVERS", "1.1.1.1").split(",") if x.strip()]
ALLOWED_IMAGES = {x.strip() for x in os.getenv("PORTAL_ALLOWED_IMAGES", "ubuntu-24.04").split(",") if x.strip()}
ALLOWED_FLAVORS = {x.strip() for x in os.getenv("PORTAL_ALLOWED_FLAVORS", "small,medium,large").split(",") if x.strip()}
BROWSER_SSH_SOURCE = os.getenv("PORTAL_BROWSER_SSH_SOURCE", "").strip()


class InstanceStateError(Exception):
    """A requested VM action is not valid for its current state."""


class InstanceQuotaError(Exception):
    """The team cannot fit the requested VM within its current quota."""


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not 3 <= len(value) <= 40:
        raise ValueError("Invalid team name")
    return value


def _connect(username: str, password: str, project: str) -> Connection:
    if not all((AUTH_URL, username, password, project)):
        raise RuntimeError("OpenStack credentials are incomplete")
    conn = Connection(
        auth_url=AUTH_URL,
        username=username,
        password=password,
        project_name=project,
        user_domain_name=DOMAIN,
        project_domain_name=PROJECT_DOMAIN,
        region_name=REGION,
        interface=INTERFACE,
        verify=VERIFY,
        app_name="school-cloud-portal",
    )
    conn.authorize()
    return conn


@lru_cache(maxsize=1)
def admin_connection() -> Connection:
    return _connect(ADMIN_USER, ADMIN_PASSWORD, os.getenv("OS_PROJECT_NAME", "admin"))


@lru_cache(maxsize=64)
def team_connection(slug: str) -> Connection:
    return _connect(SERVICE_USER, SERVICE_PASSWORD, f"portal-{slug}")


def ensure_team_project(team_name: str, slug: str, team_index: int) -> dict[str, str]:
    if not 1 <= team_index <= 200:
        raise ValueError("Team network pool exhausted")
    admin = admin_connection()
    project_name = f"portal-{slug}"
    project = admin.identity.find_project(project_name, domain_id="default", ignore_missing=True)
    if project and project.description != "School Cloud Portal managed project":
        raise ValueError("Project name is already in use")
    if not project:
        project = admin.identity.create_project(
            name=project_name,
            description="School Cloud Portal managed project",
            domain_id="default",
            is_enabled=True,
        )

    service_user = admin.identity.find_user(SERVICE_USER, domain_id="default", ignore_missing=True)
    if not service_user:
        service_user = admin.identity.create_user(
            name=SERVICE_USER,
            password=SERVICE_PASSWORD,
            domain_id="default",
            description="School Cloud Portal runtime account",
            is_enabled=True,
        )
    role = admin.identity.find_role("member", ignore_missing=False)
    if not admin.identity.validate_user_has_project_role(project, service_user, role):
        admin.identity.assign_project_role_to_user(project, service_user, role)
    team_connection.cache_clear()
    team = team_connection(slug)

    network = team.network.find_network(f"{slug}-private", ignore_missing=True)
    if not network:
        network = team.network.create_network(name=f"{slug}-private", project_id=project.id)
    if network.project_id != project.id:
        raise ValueError("Network ownership mismatch")

    cidr = f"10.30.{20 + team_index}.0/24"
    subnet = team.network.find_subnet(f"{slug}-subnet", ignore_missing=True)
    if not subnet:
        subnet = team.network.create_subnet(
            name=f"{slug}-subnet",
            network_id=network.id,
            project_id=project.id,
            ip_version=4,
            cidr=cidr,
            gateway_ip=f"10.30.{20 + team_index}.1",
            dns_nameservers=DNS_SERVERS,
            enable_dhcp=True,
        )
    if subnet.project_id != project.id or subnet.network_id != network.id or subnet.cidr != cidr:
        raise ValueError("Subnet ownership or range mismatch")

    router = admin.network.find_router(SHARED_ROUTER, ignore_missing=False)
    attached = any(
        any(ip.get("subnet_id") == subnet.id for ip in port.fixed_ips or [])
        for port in admin.network.ports(device_id=router.id)
    )
    if not attached:
        admin.network.add_interface_to_router(router, subnet_id=subnet.id)

    group = team.network.find_security_group("default", ignore_missing=True)
    if group:
        rules = list(team.network.security_group_rules(security_group_id=group.id))
        for protocol, port in (("icmp", None), ("tcp", 22)):
            found = any(
                rule.direction == "ingress" and rule.protocol == protocol
                and (port is None or rule.port_range_min == port)
                for rule in rules
            )
            if not found:
                payload = dict(security_group_id=group.id, direction="ingress",
                               protocol=protocol, remote_ip_prefix=cidr, ethertype="IPv4")
                if port is not None:
                    payload.update(port_range_min=port, port_range_max=port)
                team.network.create_security_group_rule(**payload)
        if BROWSER_SSH_SOURCE:
            source = ipaddress.ip_network(BROWSER_SSH_SOURCE, strict=True)
            if source.version != 4 or source.prefixlen != 32:
                raise ValueError("Browser SSH source must be a single IPv4 address")
            found = any(
                rule.direction == "ingress" and rule.protocol == "tcp"
                and rule.port_range_min == 22 and rule.port_range_max == 22
                and rule.remote_ip_prefix == str(source)
                for rule in rules
            )
            if not found:
                team.network.create_security_group_rule(
                    security_group_id=group.id, direction="ingress", protocol="tcp",
                    port_range_min=22, port_range_max=22,
                    remote_ip_prefix=str(source), ethertype="IPv4",
                    description="UCcloud browser SSH gateway only",
                )

    admin.compute.update_quota_set(project.id, instances=4, cores=8, ram=16384)
    return dict(project_id=project.id, network_id=network.id, subnet_id=subnet.id,
                router_id=router.id, cidr=cidr)


def _server_payload(server) -> dict:
    addresses = []
    for network_name, entries in (server.addresses or {}).items():
        for entry in entries:
            addresses.append(dict(network=network_name, address=entry.get("addr"),
                                  type=entry.get("OS-EXT-IPS:type", "fixed"), version=entry.get("version", 4)))
    flavor = server.flavor or {}
    image = server.image or {}
    return dict(id=server.id, name=server.name, status=server.status,
                created_at=str(server.created_at or ""), updated_at=str(server.updated_at or ""),
                addresses=addresses, flavor=flavor.get("original_name") or flavor.get("id", ""),
                flavor_details={key: flavor.get(key) for key in ("vcpus", "ram", "disk")},
                image=image.get("id", ""), vm_username=(server.metadata or {}).get("portal_username", ""),
                request_id=(server.metadata or {}).get("portal_request_id", ""),
                software=_software_metadata(server.metadata or {}))


def _software_metadata(metadata):
    try:
        raw = metadata.get("portal_software", "") + metadata.get("portal_software_extra", "")
        items = []
        for item in raw.split(","):
            if not item:
                continue
            parts = item.split(":")
            row = dict(id=parts[0], version=parts[1])
            if len(parts) > 2:
                row["port"] = int(parts[2])
            items.append(row)
        return validate_software(items)
    except (ValueError, TypeError, IndexError):
        return []


def list_catalog(slug: str) -> dict:
    conn = team_connection(slug)
    return {
        "software": SOFTWARE if os.getenv("PORTAL_SOFTWARE_PRESETS_ENABLED", "false").lower() == "true" else [],
        "images": [
            dict(id=image.id, name=image.name)
            for image in conn.image.images(status="active", visibility="public")
            if image.name in ALLOWED_IMAGES
        ],
        "flavors": [
            dict(id=flavor.id, name=flavor.name, vcpus=flavor.vcpus, ram=flavor.ram, disk=flavor.disk)
            for flavor in conn.compute.flavors()
            if flavor.name in ALLOWED_FLAVORS
        ],
    }


def list_servers(slug: str, project_id: str) -> list[dict]:
    conn = team_connection(slug)
    return [_server_payload(server) for server in conn.compute.servers(details=True)
            if server.project_id == project_id]


def _owned_server(conn: Connection, server_id: str, project_id: str):
    try:
        server = conn.compute.get_server(server_id)
    except NotFoundException:
        raise ValueError("Instance not found") from None
    if not server or server.project_id != project_id:
        raise ValueError("Instance not found")
    return server


def _keypair(conn: Connection, username: str, public_key: str) -> str:
    name = f"portal-{username}"
    current = conn.compute.find_keypair(name, ignore_missing=True)
    if current and current.public_key.strip() != public_key.strip():
        conn.compute.delete_keypair(current, ignore_missing=True)
        current = None
    if not current:
        conn.compute.create_keypair(name=name, public_key=public_key.strip())
    return name


def _vm_user_data(vm_username: str, vm_password: str, software: list[dict] | None = None) -> str:
    software = validate_software(software)
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{1,31}", vm_username) or vm_username == "root":
        raise ValueError("Invalid VM login ID")
    if "\n" in vm_password or "\r" in vm_password or "\x00" in vm_password:
        raise ValueError("Invalid VM password")
    password_hash = subprocess.run(
        ["/usr/bin/openssl", "passwd", "-6", "-stdin"],
        input=vm_password + "\n", text=True, capture_output=True, check=True,
    ).stdout.strip()
    if not password_hash.startswith("$6$"):
        raise RuntimeError("VM password hashing failed")
    tailscale_installer = """#!/bin/sh
set -eu
export DEBIAN_FRONTEND=noninteractive
if command -v tailscale >/dev/null 2>&1; then
    systemctl enable --now tailscaled
    exit 0
fi
apt-get -o DPkg::Lock::Timeout=180 update -qq
apt-get -o DPkg::Lock::Timeout=180 install -y -qq curl ca-certificates
install -d -m 0755 /usr/share/keyrings
curl --retry 3 --retry-delay 5 -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl --retry 3 --retry-delay 5 -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list
apt-get -o DPkg::Lock::Timeout=180 update -qq
apt-get -o DPkg::Lock::Timeout=180 install -y -qq tailscale
systemctl enable --now tailscaled
"""
    tailscale_unit = """[Unit]
Description=Install Tailscale for UCcloud VM
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/uccloud-install-tailscale
Restart=on-failure
RestartSec=120
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
"""
    config = {
        "users": ["default", {
            "name": vm_username,
            "hashed_passwd": password_hash,
            "lock_passwd": False,
            "shell": "/bin/bash",
            "groups": ["sudo"],
            "sudo": ["ALL=(ALL) ALL"],
        }],
        "ssh_pwauth": True,
        "disable_root": True,
        # Install the client only. Keep retrying after transient network failures.
        # Never enroll a student VM into the operator tailnet.
        "write_files": [
            {"path": "/usr/local/bin/uccloud-welcome", "owner": "root:root", "permissions": "0755",
             "content": (Path(__file__).parent / "guest_welcome.py").read_text(encoding="utf-8")},
            {"path": "/usr/local/bin/uccloud", "owner": "root:root", "permissions": "0755",
             "content": (Path(__file__).parent / "guest_service_cli.py").read_text(encoding="utf-8")},
            {"path": "/etc/profile.d/uccloud-welcome.sh", "owner": "root:root", "permissions": "0644",
             "content": 'case "$-" in *i*) /usr/local/bin/uccloud-welcome ;; esac\n'},
            {"path": f"/home/{vm_username}/.hushlogin", "owner": f"{vm_username}:{vm_username}",
             "permissions": "0644", "defer": True, "content": ""},
            {"path": "/usr/local/sbin/uccloud-install-tailscale", "owner": "root:root",
             "permissions": "0755", "content": tailscale_installer},
            {"path": "/etc/systemd/system/uccloud-tailscale-install.service", "owner": "root:root",
             "permissions": "0644", "content": tailscale_unit},
        ],
        "runcmd": [
            ["systemctl", "daemon-reload"],
            ["systemctl", "enable", "uccloud-tailscale-install.service"],
            ["systemctl", "start", "--no-block", "uccloud-tailscale-install.service"],
        ],
    }
    if software:
        config["write_files"].extend(software_files(vm_username, software))
        config["runcmd"].extend([
            ["systemctl", "enable", "uccloud-software-install.service"],
            ["systemctl", "start", "--no-block", "uccloud-software-install.service"],
        ])
    return base64.b64encode(("#cloud-config\n" + json.dumps(config)).encode()).decode()


def create_server(slug: str, project_id: str, username: str, network_id: str,
                  name: str, image_id: str, flavor_id: str, vm_username: str,
                  vm_password: str, public_key: str | None = None,
                  request_id: str | None = None, software: list[dict] | None = None) -> dict:
    software = validate_software(software)
    software_string = ",".join(f"{item['id']}:{item['version']}" + (f":{item['port']}" if 'port' in item else '') for item in software)
    if software and os.getenv("PORTAL_SOFTWARE_PRESETS_ENABLED", "false").lower() != "true":
        raise ValueError("프로그램 자동 설치를 준비 중입니다. 잠시 후 다시 확인해 주세요.")
    conn = team_connection(slug)
    if request_id:
        for existing in conn.compute.servers(details=True):
            if (existing.project_id == project_id
                    and (existing.metadata or {}).get("portal_request_id") == request_id):
                return _server_payload(existing)
    image = conn.image.get_image(image_id)
    flavor = conn.compute.get_flavor(flavor_id)
    network = conn.network.get_network(network_id)
    if not image or image.name not in ALLOWED_IMAGES or image.status != "active":
        raise ValueError("Image is not allowed")
    if not flavor or flavor.name not in ALLOWED_FLAVORS:
        raise ValueError("Flavor is not allowed")
    if not network or network.project_id != project_id:
        raise ValueError("Network is not owned by the team")
    if software and image.name != "ubuntu-24.04":
        raise ValueError("프로그램 자동 설치는 Ubuntu 24.04에서 사용할 수 있습니다.")
    required_ram = minimum_ram(software)
    if flavor.ram < required_ram:
        raise ValueError(f"선택한 프로그램에는 메모리 {required_ram // 1024}GB 이상이 필요합니다. 사양을 변경해 주세요.")
    remaining = quota(project_id)
    if remaining["instances"]["limit"] >= 0 and remaining["instances"]["in_use"] >= remaining["instances"]["limit"]:
        raise InstanceQuotaError("이 팀은 VM을 더 만들 수 없습니다. 기존 VM을 삭제하거나 운영자에게 문의해 주세요.")
    for resource, needed in (("cores", flavor.vcpus), ("ram", flavor.ram)):
        available = remaining[resource]["limit"] - remaining[resource]["in_use"]
        if remaining[resource]["limit"] >= 0 and available < needed:
            raise InstanceQuotaError("남은 사양이 부족합니다. 더 작은 사양을 선택하거나 기존 VM을 정리해 주세요.")
    payload = dict(
        name=name, image_id=image.id, flavor_id=flavor.id,
        networks=[{"uuid": network.id}],
        security_groups=[{"name": "default"}],
        metadata={"portal_username": vm_username, **({"portal_request_id": request_id} if request_id else {}),
                  **({"portal_software": software_string[:255]} if software else {}),
                  **({"portal_software_extra": software_string[255:]} if len(software_string) > 255 else {})},
        user_data=_vm_user_data(vm_username, vm_password, software),
    )
    if public_key:
        payload["key_name"] = _keypair(conn, username, public_key)
    server = conn.compute.create_server(**payload)
    if server.project_id and server.project_id != project_id:
        raise RuntimeError("Created instance in unexpected project")
    return _server_payload(server)


def get_console_url(slug: str, project_id: str, server_id: str) -> str:
    conn = team_connection(slug)
    server = _owned_server(conn, server_id, project_id)
    if server.status != "ACTIVE":
        raise ValueError("Instance is not running")
    console = conn.compute.create_server_remote_console(server, type="novnc", protocol="vnc")
    return console.url


def server_action(slug: str, project_id: str, server_id: str, action: str) -> None:
    conn = team_connection(slug)
    server = _owned_server(conn, server_id, project_id)
    if action not in {"start", "stop", "reboot"}:
        raise ValueError("Unsupported action")
    status = (server.status or "").upper()
    if action == "start" and status != "SHUTOFF":
        if status == "ACTIVE":
            raise InstanceStateError("VM이 이미 켜져 있습니다. '접속'을 눌러 주세요.")
        raise InstanceStateError("VM 상태가 바뀌는 중입니다. 잠시 기다려 주세요. 목록은 자동으로 갱신됩니다.")
    if action in {"stop", "reboot"} and status != "ACTIVE":
        if status == "SHUTOFF":
            raise InstanceStateError("VM이 꺼져 있습니다. '시작'을 눌러 주세요.")
        raise InstanceStateError("VM 상태가 바뀌는 중입니다. 잠시 기다려 주세요. 목록은 자동으로 갱신됩니다.")
    try:
        if action == "start":
            conn.compute.start_server(server)
        elif action == "stop":
            conn.compute.stop_server(server)
        else:
            conn.compute.reboot_server(server, reboot_type="SOFT")
    except ConflictException:
        raise InstanceStateError("VM 상태가 방금 바뀌었습니다. 잠시 기다려 주세요. 목록은 자동으로 갱신됩니다.") from None


def delete_server(slug: str, project_id: str, server_id: str) -> None:
    conn = team_connection(slug)
    server = _owned_server(conn, server_id, project_id)
    conn.compute.delete_server(server, ignore_missing=False)


def quota(project_id: str) -> dict:
    q = admin_connection().compute.get_quota_set(project_id, usage=True)
    usage = getattr(q, "usage", None)
    if not isinstance(usage, dict):
        raise RuntimeError("OpenStack quota usage is unavailable")
    result = {}
    for key in ("instances", "cores", "ram"):
        limit = getattr(q, key, None)
        in_use = usage.get(key)
        if not isinstance(limit, int) or not isinstance(in_use, int):
            raise RuntimeError(f"OpenStack quota {key} is unavailable")
        result[key] = dict(limit=limit, in_use=in_use)
    return result


def cluster_capacity() -> dict:
    """Summarize scheduler memory available on enabled compute nodes for operators."""
    admin = admin_connection()
    nodes = list(admin.compute.hypervisors(details=True))
    providers = {provider.name: provider for provider in admin.placement.resource_providers()}
    online = 0
    free_mb = 0
    memory_known = True
    for node in nodes:
        if node.state != "up" or node.status != "enabled":
            continue
        online += 1
        provider = providers.get(node.name)
        if provider is None:
            memory_known = False
            continue
        inventory = next((item for item in admin.placement.resource_provider_inventories(provider)
                          if item.resource_class == "MEMORY_MB"), None)
        if inventory is None:
            memory_known = False
            continue
        used = admin.placement.fetch_resource_provider_usages(provider).usages.get("MEMORY_MB")
        if not isinstance(used, int):
            memory_known = False
            continue
        free_mb += max(0, int((inventory.total - inventory.reserved) * inventory.allocation_ratio) - used)
    return {"online": online, "total": len(nodes),
            "memory_free_mb": free_mb if memory_known else None}

