"""Policy tests for SPEC-SEC-DOCKER-AUTHZ-001.

The hostile bodies below are not invented. They are the exact shape proven to
escalate on 2026-08-14 against `tecnativa/docker-socket-proxy:v0.5.0` running
production's env: the create returned 201, the start returned 204, and the
container read the host's `/etc/hostname`.

The legitimate bodies are equally real — taken from
`infrastructure.py::_start_librechat_container` and Vexa's
`runtime_kernel/docker_backend.py::start`. A policy that rejects the attack but
also rejects tenant provisioning is not a fix, so both directions are tested.
"""

from __future__ import annotations

import pytest

from app.policy import PORTAL_API, VEXA_RUNTIME, Policy, PolicyViolation, check_create

# ── The proven escalation ────────────────────────────────────────────────────

PROVEN_ESCALATION = {
    "Image": "alpine:3.22",
    "Cmd": ["sh", "-c", "cat /host/etc/hostname"],
    "HostConfig": {"Binds": ["/:/host"], "Privileged": True, "PidMode": "host"},
}


@pytest.mark.parametrize("policy", [PORTAL_API, VEXA_RUNTIME], ids=lambda p: p.name)
def test_the_proven_escalation_is_refused_for_every_principal(policy: Policy) -> None:
    with pytest.raises(PolicyViolation):
        check_create(PROVEN_ESCALATION, policy)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("Privileged", True),
        ("CapAdd", ["SYS_ADMIN"]),
        ("Devices", [{"PathOnHost": "/dev/sda"}]),
        ("PidMode", "host"),
        ("IpcMode", "host"),
        ("UsernsMode", "host"),
        ("SecurityOpt", ["apparmor=unconfined"]),
        ("Sysctls", {"kernel.shmmax": "1"}),
        ("CgroupParent", "/"),
        ("Runtime", "runc-but-worse"),
        ("DeviceCgroupRules", ["a *:* rwm"]),
    ],
)
def test_each_host_root_field_is_refused_individually(key: str, value: object) -> None:
    """One field at a time — a policy that only catches the combination is not a policy."""
    with pytest.raises(PolicyViolation, match=key):
        check_create({"Image": "x", "HostConfig": {key: value}}, PORTAL_API)


@pytest.mark.parametrize("mode", ["host", "none"])
def test_host_network_mode_is_refused(mode: str) -> None:
    with pytest.raises(PolicyViolation, match="NetworkMode"):
        check_create({"Image": "x", "HostConfig": {"NetworkMode": mode}}, PORTAL_API)


# ── Falsy defaults must NOT be denials ───────────────────────────────────────


def test_client_library_falsy_defaults_are_allowed() -> None:
    """docker-py sends these on every create; denying them breaks all provisioning.

    This is the failure mode that would make the whole service a rollback: a
    policy that reads "key present" instead of "key requested" refuses every
    legitimate call while looking correct in a hostile-input test.
    """
    check_create(
        {
            "Image": "x",
            "HostConfig": {
                "Privileged": False,
                "CapAdd": None,
                "Devices": [],
                "SecurityOpt": None,
                "Sysctls": {},
                "PidMode": "",
                "NetworkMode": "",
            },
        },
        PORTAL_API,
    )


def test_body_without_host_config_is_allowed() -> None:
    check_create({"Image": "x", "Cmd": ["true"]}, PORTAL_API)


# ── portal-api: the real tenant-provisioning body ────────────────────────────

LIBRECHAT_CREATE = {
    "Image": "ghcr.io/danny-avila/librechat:v0.8.7",
    "Entrypoint": ["/bin/sh", "/klai-entrypoint.sh"],
    "Cmd": ["npm", "run", "backend"],
    "Labels": {
        "klai.managed_by": "portal-api-provisioning",
        "klai.tenant_slug": "voys",
        "klai.kind": "librechat",
    },
    "HostConfig": {
        "Binds": [
            "/opt/klai/librechat/voys/.env:/app/.env:ro",
            "/opt/klai/librechat/voys/librechat.yaml:/app/librechat.yaml:ro",
            "/opt/klai/librechat/voys/images:/app/client/public/images:rw",
            "/opt/klai/librechat/patches/format.cjs:/app/.../format.cjs:ro",
            "/opt/klai/librechat/klai-entrypoint.sh:/klai-entrypoint.sh:ro",
        ],
        "RestartPolicy": {"Name": "unless-stopped"},
        "NetworkMode": "klai-net",
    },
}


def test_real_tenant_provisioning_body_is_allowed() -> None:
    check_create(LIBRECHAT_CREATE, PORTAL_API)


@pytest.mark.parametrize(
    "bind",
    [
        "/etc/shadow:/x:ro",
        "/opt/klai/../etc:/x:ro",
        "/opt/klai/librechat/../../root:/x:ro",
        "/var/run/docker.sock:/var/run/docker.sock:rw",
        "/opt/klai-other/thing:/x:ro",  # prefix is a string-prefix but not a path-prefix
    ],
)
def test_portal_api_binds_outside_its_prefix_are_refused(bind: str) -> None:
    body = {"Image": "x", "HostConfig": {"Binds": [bind]}}
    with pytest.raises(PolicyViolation, match="outside"):
        check_create(body, PORTAL_API)


def test_mounts_api_is_policed_as_well_as_binds() -> None:
    """Docker takes binds two ways; checking only `Binds` is a bypass, not a policy."""
    body = {
        "Image": "x",
        "HostConfig": {"Mounts": [{"Type": "bind", "Source": "/", "Target": "/host"}]},
    }
    with pytest.raises(PolicyViolation, match="outside"):
        check_create(body, PORTAL_API)


def test_named_volume_mounts_are_not_host_paths() -> None:
    """A named volume is daemon-managed and cannot name a host path — allow it."""
    body = {
        "Image": "x",
        "HostConfig": {"Mounts": [{"Type": "volume", "Source": "some-vol", "Target": "/data"}]},
    }
    check_create(body, PORTAL_API)


# ── vexa12-runtime: no binds at all ──────────────────────────────────────────

BOT_SPAWN = {
    "Image": "vexaai/vexa-bot:v0.12.22",
    "HostConfig": {"NetworkMode": "vexa12-bots", "ShmSize": 2147483648},
}


def test_real_bot_spawn_body_is_allowed() -> None:
    check_create(BOT_SPAWN, VEXA_RUNTIME)


def test_bot_runtime_may_not_bind_anything() -> None:
    """Klai does not deploy Vexa's agent feature, so a bind here is never legitimate.

    Its three bind sources (workspace_binds, HOST_CLAUDE_CREDENTIALS,
    VEXA_AGENT_SRC_MOUNT) are all agent-feature paths; AGENT_IMAGE is empty and
    neither env var is set on core-01. A spawn that suddenly carries one is a
    compromise or an undocumented feature change, and both should stop here.
    """
    body = {"Image": "x", "HostConfig": {"Binds": ["/opt/klai/librechat/voys/.env:/x:ro"]}}
    with pytest.raises(PolicyViolation, match="may not bind any host path"):
        check_create(body, VEXA_RUNTIME)


def test_bot_runtime_may_not_escape_its_network() -> None:
    body = {"Image": "x", "HostConfig": {"NetworkMode": "klai-net"}}
    with pytest.raises(PolicyViolation, match="NetworkMode"):
        check_create(body, VEXA_RUNTIME)


def test_principals_do_not_inherit_each_others_allowance() -> None:
    """REQ-U-002. The librechat body is fine for portal-api and never for the bot runtime."""
    check_create(LIBRECHAT_CREATE, PORTAL_API)
    with pytest.raises(PolicyViolation):
        check_create(LIBRECHAT_CREATE, VEXA_RUNTIME)


# ── Malformed input fails closed ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "host_config",
    [
        {"Binds": [{"not": "a string"}]},
        {"Binds": [":/dst:ro"]},
        {"Mounts": ["not an object"]},
        {"Mounts": [{"Type": "bind", "Target": "/x"}]},
    ],
)
def test_malformed_bind_specs_are_refused(host_config: dict) -> None:
    with pytest.raises(PolicyViolation):
        check_create({"Image": "x", "HostConfig": host_config}, PORTAL_API)


def test_host_config_of_the_wrong_type_is_refused() -> None:
    with pytest.raises(PolicyViolation, match="not an object"):
        check_create({"Image": "x", "HostConfig": "surely not"}, PORTAL_API)
