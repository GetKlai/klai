"""Per-principal authorization policy for Docker container-create.

SPEC-SEC-DOCKER-AUTHZ-001.

`docker-socket-proxy` authorises on method + path. `POST /containers/create` is
on its allow-list and its BODY is never parsed — but `HostConfig` lives in that
body, and `HostConfig` is where container isolation is decided. Proven on
2026-08-14 against `tecnativa/docker-socket-proxy:v0.5.0` with production's exact
env (`CONTAINERS=1 NETWORKS=1 POST=1 DELETE=1`): a create carrying
`Binds: ["/:/host"]`, `Privileged: true`, `PidMode: "host"` returned 201, started
204, and the container read the host's `/etc/hostname`.

This module is the missing check. It is deliberately a pure function over the
parsed body: no I/O, no globals, trivially testable, and the same verdict whether
it runs in the proxy or in a unit test.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# HostConfig keys that hand a container capability over the host. Presence with a
# truthy/non-empty value is a denial for EVERY principal — none of Klai's two
# callers has a legitimate use, and a future one should have to change this list
# in a reviewable diff rather than inherit the allowance silently.
#
# The values are checked, not just the keys: Docker's client libraries send many
# of these as explicit falsy defaults (`Privileged: false`, `CapAdd: null`), and
# denying those would break every legitimate create.
FORBIDDEN_HOST_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "Privileged",  # full host capability
        "CapAdd",  # e.g. SYS_ADMIN — mount(), ptrace of host procs
        "Devices",  # raw device access, e.g. the host disk
        "DeviceCgroupRules",
        "CgroupParent",  # escape the deployment's cgroup accounting
        "PidMode",  # "host" — see and signal host processes
        "IpcMode",  # "host" — shared memory with the host
        "UsernsMode",  # "host" — defeat user-namespace remapping
        "SecurityOpt",  # apparmor=unconfined / seccomp=unconfined
        "Sysctls",  # host-scoped kernel tunables
        "Runtime",  # swap in a runtime without isolation
    }
)

# NetworkMode values that place the container in the host's network namespace.
FORBIDDEN_NETWORK_MODES: frozenset[str] = frozenset({"host", "none"})


class PolicyViolation(Exception):
    """A create was refused. The message is operator-facing and MUST name the field.

    REQ-U-003: a denial fails loudly and says which field caused it. A silently
    stripped field is harder to diagnose than a refusal and hides a real attack.
    """


@dataclass(frozen=True)
class Policy:
    """What one principal may ask Docker to create.

    Args:
        name: principal identity, used in denial messages and logs.
        allowed_bind_prefixes: host path prefixes a bind source may resolve under.
            Empty means the principal may not bind anything at all.
        allowed_network_modes: NetworkMode values this principal may request.
            Empty means any non-forbidden value is acceptable.
    """

    name: str
    allowed_bind_prefixes: tuple[str, ...] = ()
    allowed_network_modes: frozenset[str] = field(default_factory=frozenset)


def _normalise(path: str) -> str:
    """Collapse `..`, `.` and duplicate separators without touching the filesystem.

    Deliberately lexical. `os.path.realpath` would resolve symlinks against the
    PROXY's filesystem, not the daemon's, and the two are different mount
    namespaces — a wrong answer with false confidence. Symlink evasion is handled
    by mounting the allowed prefixes read-only into the proxy (see
    `resolve_within_prefix`); when that mount is absent the lexical check still
    holds the line against `..` traversal.
    """
    return os.path.normpath(path)


def resolve_within_prefix(source: str, prefixes: tuple[str, ...]) -> bool:
    """Is this bind source inside one of the allowed prefixes?

    Two layers:

    1. Lexical containment after `normpath`, which defeats `/opt/klai/librechat/../../etc`.
    2. If the prefix is actually visible to this process (the compose file mounts
       the allowed prefixes read-only), `realpath` both sides so a symlink planted
       INSIDE the prefix that points outside it is caught too. Without that mount
       layer 1 stands alone and a symlink under the prefix would pass — which is
       why the mount is part of the deployment, not an optimisation.
    """
    candidate = _normalise(source)
    for prefix in prefixes:
        clean_prefix = _normalise(prefix).rstrip("/")
        if candidate != clean_prefix and not candidate.startswith(clean_prefix + "/"):
            continue
        if os.path.isdir(clean_prefix):
            real_prefix = os.path.realpath(clean_prefix)
            real_candidate = os.path.realpath(candidate)
            if real_candidate != real_prefix and not real_candidate.startswith(real_prefix + os.sep):
                continue
        return True
    return False


def _bind_sources(host_config: dict) -> list[str]:
    """Every host path this HostConfig would mount, from both mount APIs.

    Docker accepts binds two ways and a policy that checks only one is not a
    policy: `Binds` (["src:dst:mode"]) and `Mounts` ([{Type, Source, Target}]).
    Vexa's runtime uses BOTH — `Mounts` for volume subpaths, `Binds` otherwise.
    """
    sources: list[str] = []

    for entry in host_config.get("Binds") or []:
        if not isinstance(entry, str):
            raise PolicyViolation(f"HostConfig.Binds entry is not a string: {entry!r}")
        # "src:dst" / "src:dst:ro". A Windows-style drive letter cannot occur on
        # this daemon, so splitting on the first colon is unambiguous.
        source = entry.split(":", 1)[0]
        if not source:
            raise PolicyViolation(f"HostConfig.Binds entry has an empty source: {entry!r}")
        sources.append(source)

    for entry in host_config.get("Mounts") or []:
        if not isinstance(entry, dict):
            raise PolicyViolation(f"HostConfig.Mounts entry is not an object: {entry!r}")
        # Named volumes are daemon-managed and cannot reach the host filesystem
        # by path; only bind mounts name a host path.
        if entry.get("Type") not in ("bind", None):
            continue
        source = entry.get("Source") or ""
        if not source:
            raise PolicyViolation(f"HostConfig.Mounts bind has no Source: {entry!r}")
        sources.append(source)

    return sources


def check_create(body: dict, policy: Policy) -> None:
    """Raise PolicyViolation if this container-create body is not allowed.

    Returns None on success. Called for `POST /containers/create` only; every
    other endpoint keeps passing through to docker-socket-proxy, whose path
    whitelist is unchanged and still the first line of defence.
    """
    host_config = body.get("HostConfig")
    if host_config is None:
        return
    if not isinstance(host_config, dict):
        raise PolicyViolation("HostConfig is present but not an object")

    for key in sorted(FORBIDDEN_HOST_CONFIG_KEYS):
        value = host_config.get(key)
        # Falsy is the client-library default (Privileged=false, CapAdd=null) and
        # is what every legitimate create sends. Only a real request is denied.
        if value:
            raise PolicyViolation(
                f"{policy.name} may not set HostConfig.{key} (got {value!r}) — host-root-equivalent capability"
            )

    network_mode = host_config.get("NetworkMode")
    if network_mode:
        if network_mode in FORBIDDEN_NETWORK_MODES:
            raise PolicyViolation(
                f"{policy.name} may not set HostConfig.NetworkMode={network_mode!r} — "
                "places the container in the host network namespace"
            )
        if policy.allowed_network_modes and network_mode not in policy.allowed_network_modes:
            raise PolicyViolation(
                f"{policy.name} may not set HostConfig.NetworkMode={network_mode!r} — "
                f"allowed: {sorted(policy.allowed_network_modes)}"
            )

    sources = _bind_sources(host_config)
    if sources and not policy.allowed_bind_prefixes:
        raise PolicyViolation(f"{policy.name} may not bind any host path (requested: {sources})")
    for source in sources:
        if not resolve_within_prefix(source, policy.allowed_bind_prefixes):
            raise PolicyViolation(
                f"{policy.name} may not bind {source!r} — outside {list(policy.allowed_bind_prefixes)}"
            )


# ── The two principals ───────────────────────────────────────────────────────
#
# Each is reached on its own listener port rather than by inferring identity from
# a TCP peer address: portal-api connects over the socket-proxy network, the Vexa
# runtime over a socat unix-socket bridge, and the bridge would make every
# request look like the sidecar. A port per principal is deterministic and needs
# no identity guessing (SPEC open questions 2 and 3, answered by construction).

PORTAL_API = Policy(
    name="portal-api",
    # Every bind in _start_librechat_container sits under this one prefix: the
    # tenant .env, librechat.yaml, images/, the shared patch mounts and the
    # entrypoint wrapper. Verified against the callsite, not assumed.
    allowed_bind_prefixes=("/opt/klai/librechat",),
    allowed_network_modes=frozenset(),  # provisioning attaches networks separately
)

VEXA_RUNTIME = Policy(
    name="vexa12-runtime",
    # No binds at all. The runtime's three bind sources (workspace_binds,
    # HOST_CLAUDE_CREDENTIALS, VEXA_AGENT_SRC_MOUNT) all belong to Vexa's agent
    # feature, which Klai does not deploy — AGENT_IMAGE is empty and neither env
    # var is set on core-01. A bot spawn that suddenly carries one is either a
    # compromise or an undocumented feature change; both should stop here.
    allowed_bind_prefixes=(),
    allowed_network_modes=frozenset({"vexa12-bots"}),
)
