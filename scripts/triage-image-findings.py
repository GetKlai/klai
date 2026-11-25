#!/usr/bin/env python3
"""Reduce the Trivy findings on third-party images to the ones someone acts on.

The weekly scan of every external image pinned in compose produced 1,275 open
code-scanning alerts on 2026-11-24 (442 distinct ids). A list that long is not
acted on. This script decides, per image, which findings go to the
code-scanning tab and why; the full Trivy result stays a workflow artifact.

A finding is DROPPED when
  - its package comes from an SBOM file inside the image that the image's own
    installed packages contradict (see stale_sbom_layers), or
  - its package is a header-only kernel package (see KERNEL_HEADER_PACKAGES), or
  - a statement in .vex/unused-components.json says the component is present
    but never executed for this service (see vex_match).
Everything else is KEPT when any clause of RULE holds (see keep_reasons):
  in the CISA KEV catalog; EPSS at or above the threshold; critical on an image
  that is exposed, consumes untrusted web content, or cannot be classified; or
  critical, fixable and older than its age limit.

Exposure comes from the repository, not from a hand-kept list of what is
public: every `reverse_proxy` upstream in the static Caddyfile and in the code
that writes tenant routes at runtime, plus every port published on all host
interfaces. deploy/image-exposure.json names the services that consume
untrusted content and the ones that are internal, each with a reason; a
service in neither list and not derived as exposed is treated as exposed and
reported by name. A file that mentions reverse_proxy and is not listed as a
route source (or as not one) stops the run, because a new route writer that
nobody classified is exactly how an exposed image would end up "internal".

Subcommands (all JSON in, JSON or Markdown out):
  targets   compose JSON -> scan targets (same keys and categories as before)
  stale     open alerts + targets -> categories nobody uploads any more
  kev       the CISA KEV catalog, downloaded once per run (fails on an outage)
  triage    one Trivy result + EPSS for its ids -> triaged Trivy JSON, summary
            and OpenVEX (kept on the runner) and a counts-only aggregate
  summary   aggregates -> totals in Markdown; --fail-on-kev
  changed   base and head targets -> pins a pull request changes
  gate      base and head summaries -> fail on a KEV finding the change adds

Public surfaces (logs, step summaries, artifacts, issues) carry counts only;
the detail goes to the code-scanning tab as SARIF. See "Reporting" below.
  validate  .vex/unused-components.json and deploy/image-exposure.json

Standard library only. Tests: python3 -m unittest scripts/tests/test_triage_image_findings.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# --- The rule. One place; every number carries its source. -------------------
#
# KEV: CISA's catalog of vulnerabilities exploited in the wild. BOD 26-04
#   (which replaced BOD 22-01) starts the remediation clock when CISA adds a CVE
#   to the catalog; its standard tier for a KEV entry is 14 days.
#   https://www.cisa.gov/news-events/directives/bod-26-04-prioritizing-security-updates-based-risk
# EPSS: FIRST's probability of exploitation in the next 30 days. FIRST gives
#   no universal cut-off; it puts the base rate near 2% and the effort of
#   "every CVSS critical" at about 0.04. 0.1 is above that, so this clause
#   adds a short list instead of re-creating the long one; on 2026-11-24 it
#   selected 5 of 442 ids. https://www.first.org/epss/using-epss
# Age limits: FedRAMP continuous monitoring gives 30 days for critical and
#   high findings and 90 days for moderate ones. Critical on an exposed image
#   gets 30; critical on an internal image is treated one tier lower (90),
#   the same exposure step BOD 26-04 uses in its decision table.
#   https://www.fedramp.gov/resources/documents/Continuous_Monitoring_Playbook.pdf
EPSS_THRESHOLD = 0.1
AGE_LIMIT_DAYS = {"kev": 14, "critical-exposed": 30, "critical-internal": 90}

KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
# The cve parameter accepts at most 2000 characters including commas, and a
# response page holds 100 rows by default (https://api.first.org/epss).
# Unauthenticated callers get 1000 requests a minute (https://api.first.org/);
# a weekly run needs about five.
EPSS_URL = "https://api.first.org/data/v1/epss"
EPSS_BATCH = 100
EPSS_MAX_PARAM = 2000
FEED_ATTEMPTS = 3
USER_AGENT = "image-findings-triage"

# OpenVEX v0.2.0 status justifications (OPENVEX-SPEC.md, "Status Justifications").
JUSTIFICATIONS = {
    "component_not_present",
    "vulnerable_code_not_present",
    "vulnerable_code_not_in_execute_path",
    "vulnerable_code_cannot_be_controlled_by_adversary",
    "inline_mitigations_already_exist",
}
VEX_MAX_DAYS = 365  # the same ceiling .trivyignore.yaml entries have
REASON_MIN_CHARS = 40

RANK = {"internal": 0, "reachable": 1, "content": 2, "exposed": 3, "unknown": 4}
# Criticals and high exploit-prediction scores count on all of these alike.
EXPOSED_LIKE = {"reachable", "content", "exposed", "unknown"}

# The upload job's analysis key; GitHub keeps one alert set per tool, key and
# category, so stale categories are only those of this job.
SCAN_JOB_KEY = ".github/workflows/scan-pinned-images.yml:scan"

# Same filter the workflow's jq used before this script existed.
EXCLUDED_IMAGE = re.compile(r"^(ghcr\.io/getklai/[^@]+$|[^:]+:klai$|[^:]+:local$|[^:]+:.*-local-[0-9]{6}-[0-9]{4}$)")
CRAWL4AI_IMAGE = re.compile(r"^ghcr\.io/getklai/crawl4ai:")
# The crawler image is built on the host and never pushed, so the scan builds
# deploy/crawl4ai itself and scans that build from the local Docker daemon.
# Scanning the upstream FROM image instead counted the build checkout our
# Dockerfile deletes (302 of its 308 findings on 2026-11-24).
CRAWL4AI_BUILD = {"image": "klai-scan/crawl4ai:build", "build": "deploy/crawl4ai"}
ROUTE_LINE = re.compile(r"^\s*reverse_proxy\s+(.*)$", re.M)
TOOL_FILES = {"scripts/triage-image-findings.py", "scripts/validate-vex.sh", "deploy/image-exposure.json"}


class TriageError(Exception):
    """A condition under which the triage would be wrong; the run must stop."""


def load(path):
    return json.loads(Path(path).read_text())


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def today():
    return dt.datetime.now(dt.UTC).date()


# --- Targets ----------------------------------------------------------------


IMAGE_ENV = re.compile(r"(^|_)IMAGE$")
IMAGE_OVERRIDES_ENV = re.compile(r"_IMAGE_OVERRIDES$")
IMAGE_REF = re.compile(r"^[a-z0-9][a-z0-9._/-]*(:[\w.-]+)?(@sha256:[0-9a-f]{64})?$")
SETTING_DEFAULT = re.compile(r'^\s*(\w*image\w*)\s*:\s*str\s*=\s*"([^"]*)"', re.M)


def image_refs(composes, settings_text=""):
    """Every image the stack can start, whatever the syntax of its pin.

    Returns (source, owner, kind, image) for `image:` keys, environment values
    named *_IMAGE (the meeting-bot image is pinned only as BROWSER_IMAGE on its
    runtime, the tenant chat image as LIBRECHAT_IMAGE on portal-api), the
    slug=image lists in *_IMAGE_OVERRIDES, and the image defaults in the
    portal's settings that compose does not override. A value that is not a
    plain image reference stops the run instead of dropping the image.
    """
    refs = []
    for source, compose in composes.items():
        for service, spec in ((compose or {}).get("services") or {}).items():
            if (spec or {}).get("image"):
                refs.append((source, service, "image", spec["image"]))
            for var, value in env_of(spec).items():
                values = []
                if IMAGE_OVERRIDES_ENV.search(var):
                    values = [entry.partition("=")[2].strip() for entry in value.split(",") if entry.strip()]
                elif IMAGE_ENV.search(var) and value:
                    values = [value]
                for image in values:
                    if not IMAGE_REF.match(image):
                        raise TriageError(f"{source}/{service} {var} is not a plain image reference, so no scan can cover it")
                    refs.append((source, f"{service}.{var}", "env", image))
    portal_env = env_of(((composes.get("deploy") or {}).get("services") or {}).get("portal-api"))
    for name, image in SETTING_DEFAULT.findall(settings_text):
        if name.upper() not in portal_env and IMAGE_REF.match(image) and (":" in image or "@" in image):
            refs.append(("deploy", f"portal-api.{name.upper()}", "default", image))
    return refs


def context_hash(repo, build):
    """sha256 over the files of a build context, so a change to the crawler's
    Dockerfile or patch counts as a changed pin for the gate."""
    root = Path(repo) / build
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def targets(composes, settings_text="", repo="."):
    groups, builds = {}, {}
    for source, owner, kind, image in image_refs(composes, settings_text):
        if CRAWL4AI_IMAGE.search(image):
            image = CRAWL4AI_BUILD["image"]
            builds[image] = CRAWL4AI_BUILD["build"]
        if EXCLUDED_IMAGE.search(image):
            continue
        groups.setdefault(image, []).append((source, owner, kind))
    out = []
    for image, members in groups.items():
        # The key (and so the category GitHub correlates alerts by) comes from
        # the `image:` members when there are any, so an environment reference
        # to an image a service already runs does not rename its category.
        named = [m for m in members if m[2] == "image"] or members
        key = "__".join(sorted(f"{s}_{o}" for s, o, _ in named))
        out.append({
            "key": key,
            "services": sorted({f"{s}/{o}" for s, o, _ in members}),
            "image": image,
            "build": builds.get(image, ""),
            "context": context_hash(repo, builds[image]) if image in builds else "",
            "category": "pinned-service-" + re.sub(r"[^A-Za-z0-9_.-]", "_", key),
        })
    return sorted(out, key=lambda t: t["key"])


def load_json5(text):
    """Parse the JSON5 subset renovate.json5 uses: comments, single quotes,
    unquoted keys, trailing commas and backslash line continuations."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j, buf = i + 1, []
            while text[j] != c:
                if text[j] == "\\":
                    nxt = text[j + 1]
                    if nxt == "\n":
                        j += 2
                        continue
                    buf.append("\\" + nxt if nxt != "'" else "'")
                    j += 2
                    continue
                buf.append('\\"' if text[j] == '"' else text[j])
                j += 1
            out.append('"' + "".join(buf) + '"')
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i) if "\n" in text[i:] else n
        elif text.startswith("/*", i):
            i = text.index("*/", i) + 2
        elif c.isalpha() or c in "_$":
            m = re.match(r"[A-Za-z_$][\w$]*", text[i:])
            word = m.group(0)
            out.append(word if word in ("true", "false", "null") else f'"{word}"')
            i += len(word)
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def unrecognised_renovate_pins(renovate, files, recognised):
    """Lines Renovate's custom regex managers treat as image pins that the
    target derivation does not know.

    `files` maps the stack's pin files (compose files, portal settings) to
    their text. A match there whose line value is not an image the derivation
    produced (scanned or deliberately excluded) means an image pin in a
    syntax this script does not read, and the gate fails closed on it.
    """
    errors = []
    for manager in renovate.get("customManagers") or []:
        if manager.get("customType") != "regex":
            continue
        patterns = [re.compile(p.strip("/")) for p in manager.get("managerFilePatterns") or []]
        for path, text in files.items():
            if not any(p.search(path) for p in patterns):
                continue
            for pattern in manager.get("matchStrings") or []:
                for m in re.finditer(pattern.replace("(?<", "(?P<"), text):
                    start = text.rfind("\n", 0, m.start()) + 1
                    end = text.find("\n", m.start())
                    line = text[start:end if end >= 0 else None].split("#", 1)[0].strip()
                    value = re.split(r"[:=]\s*", line, maxsplit=1)[-1].strip().strip("'\"")
                    if value not in recognised:
                        lineno = text.count("\n", 0, m.start()) + 1
                        errors.append(f"{path}:{lineno} is an image pin to Renovate that the scan targets do not include")
    return errors


def stale_categories(alerts, current):
    """Categories of the scan job with open alerts that no current target uploads.

    GitHub correlates alerts by tool and category, so a category that stopped
    being uploaded (a renamed key, a removed service) keeps its alerts open for
    ever. 37 of the 1,275 alerts on 2026-11-24 sat in five such categories.
    """
    seen = set()
    for alert in alerts:
        inst = alert.get("most_recent_instance") or {}
        if alert.get("state") != "open" or (alert.get("tool") or {}).get("name") != "Trivy":
            continue
        if inst.get("analysis_key") != SCAN_JOB_KEY:
            continue
        category = inst.get("category") or ""
        if category.startswith("pinned-") and category not in current:
            seen.add(category)
    return sorted(seen)


# --- Exposure ---------------------------------------------------------------


def env_of(spec):
    env = (spec or {}).get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) if "=" in item else (item, "") for item in env)
    return {k: "" if v is None else str(v) for k, v in env.items()}


def networks_of(spec):
    nets = (spec or {}).get("networks") or []
    return set(nets if isinstance(nets, list) else nets.keys())


def route_hosts(text):
    hosts = []
    for line in ROUTE_LINE.findall(text):
        for token in line.split():
            if token == "{" or token.startswith("#"):
                break
            if token.startswith("@"):
                continue
            token = re.sub(r"^[a-z0-9]+://", "", token)
            hosts.append(token.rsplit(":", 1)[0] if ":" in token else token)
    return hosts


def public_port(port):
    if isinstance(port, dict):
        return port.get("host_ip") in (None, "", "0.0.0.0", "::")
    parts = str(port).split(":")
    return len(parts) < 3 or parts[0] in ("", "0.0.0.0", "[::]")


def is_tool_or_test(path):
    parts = path.split("/")
    name = parts[-1]
    return (
        path in TOOL_FILES
        or path.endswith(".md")
        or name.startswith("test")
        or any(p in ("tests", "test") for p in parts[:-1])
    )


def unlisted_route_sources(files_with_mention, cfg):
    listed = set(cfg["route_sources"]) | set(cfg["not_route_sources"])
    return sorted(f for f in files_with_mention if not is_tool_or_test(f) and f not in listed)


def find_route_mentions(repo):
    files = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True
    ).stdout.decode().split("\0")
    found = []
    for name in filter(None, files):
        path = Path(repo) / name
        try:
            if re.search(r"\breverse_proxy\b", path.read_text(errors="ignore")):
                found.append(name)
        except (IsADirectoryError, FileNotFoundError):
            continue
    return found


def classify(deploy, dev, cfg, route_texts, gpu=None):
    """Return {"<source>/<owner>": (class, reason)} for every service and every
    environment-pinned image ("<service>.<VAR>") in the compose files.

    Classes, most exposed first: exposed (derived from the proxy routes, the
    runtime route writers and public ports), content (listed: parses
    untrusted input), reachable (derived: shares a compose network with an
    exposed or content-consuming service, so one hop behind it, as a
    pre-authentication injection through a public application would be),
    internal (listed), unknown (none of these; treated as exposed).
    """
    services = deploy.get("services") or {}
    exposed = {}
    for path, text in sorted(route_texts.items()):
        for host in route_hosts(text):
            if host in services:
                exposed.setdefault(host, f"reverse-proxy upstream in {path}")
                continue
            runtime = cfg["runtime_upstreams"].get(host)
            if runtime is None:
                raise TriageError(
                    f"{path} routes to {host!r}, which is neither a compose service nor a "
                    "runtime_upstreams entry in deploy/image-exposure.json"
                )
            env = env_of(services.get(runtime["service"]))
            image = env.get(runtime["env"], "")
            if not image or "${" in image:
                raise TriageError(
                    f"{path} routes to {host!r}, whose image comes from {runtime['env']} of "
                    f"{runtime['service']}, and compose does not pin that value"
                )
            reason = f"{runtime['reason']} ({path})"
            for var in (runtime["env"], runtime.get("overrides_env")):
                if var:
                    exposed.setdefault(f"{runtime['service']}.{var}", reason)
            for service, spec in services.items():
                if (spec or {}).get("image") == image:
                    exposed.setdefault(service, reason)
    for service, spec in services.items():
        if (spec or {}).get("network_mode") == "host":
            exposed.setdefault(service, "runs in the host network namespace")
        for port in (spec or {}).get("ports") or []:
            if public_port(port):
                exposed.setdefault(service, f"publishes {port} on every host interface")
    owners = list(services)
    for _, owner, kind, _ in image_refs({"deploy": deploy}):
        if kind == "env" and owner not in owners:
            owners.append(owner)
    facing = {s for s in services if s in exposed or s in cfg["content_consumers"]}
    nets = {s: networks_of(spec) or {"default"} for s, spec in services.items()}
    classes = {}
    for owner in owners:
        if owner in exposed:
            classes[f"deploy/{owner}"] = ("exposed", exposed[owner])
        elif owner in cfg["content_consumers"]:
            classes[f"deploy/{owner}"] = ("content", cfg["content_consumers"][owner])
        elif owner in nets and (peer := next((f for f in sorted(facing) if nets[f] & nets[owner]), None)):
            net = sorted(nets[peer] & nets[owner])[0]
            classes[f"deploy/{owner}"] = ("reachable", f"shares network {net} with {peer}")
        elif owner in cfg["internal"]:
            classes[f"deploy/{owner}"] = ("internal", cfg["internal"][owner])
        else:
            classes[f"deploy/{owner}"] = ("unknown", "not classified in deploy/image-exposure.json, treated as exposed")
    for service in (dev or {}).get("services") or {}:
        classes[f"dev/{service}"] = ("internal", "docker-compose.dev.yml runs on developer machines only")
    for service in (gpu or {}).get("services") or {}:
        key = f"gpu/{service}"
        for section, cls in (("content_consumers", "content"), ("internal", "internal")):
            if key in cfg[section]:
                classes[key] = (cls, cfg[section][key])
        classes.setdefault(key, ("unknown", "not classified in deploy/image-exposure.json, treated as exposed"))
    return classes


def image_exposure(target, classes):
    per = {s: classes.get(s, ("unknown", "not in compose, treated as exposed")) for s in target["services"]}
    worst = max((c for c, _ in per.values()), key=RANK.__getitem__)
    return worst, {s: f"{c}: {r}" for s, (c, r) in per.items()}


# --- VEX --------------------------------------------------------------------


def condition_failures(stmt, deploy, dev):
    composes = {"deploy": (deploy or {}).get("services") or {}, "dev": (dev or {}).get("services") or {}}
    failures = []
    for cond in stmt.get("conditions") or []:
        source, _, name = cond["service"].partition("/")
        spec = composes.get(source, {}).get(name)
        if spec is None:
            failures.append(f"{cond['service']} is not a compose service")
            continue
        env = env_of(spec)
        if (cond.get("env_unset") or cond.get("env_in")) and spec.get("env_file"):
            failures.append(f"{cond['service']} has an env_file, which could set the variables this statement relies on")
        for var in cond.get("env_unset") or []:
            if env.get(var):
                failures.append(f"{cond['service']} sets {var}")
        for var, allowed in (cond.get("env_in") or {}).items():
            if env.get(var, "") not in allowed:
                failures.append(f"{cond['service']} sets {var}={env.get(var)!r}, not one of {allowed}")
        for net in cond.get("networks_exclude") or []:
            if net in networks_of(spec):
                failures.append(f"{cond['service']} joins network {net}")
        if cond.get("entrypoint_unchanged") and (spec.get("entrypoint") or spec.get("command")):
            failures.append(f"{cond['service']} overrides the image's entrypoint or command")
    return failures


def image_file_contains(image, path, text):
    """True when `path` inside `image` contains `text` (read with the container runtime)."""
    done = subprocess.run(
        ["docker", "run", "--rm", "--platform", "linux/amd64", "--entrypoint", "cat", image, path],
        capture_output=True, text=True, timeout=600, check=False,
    )
    return done.returncode == 0 and text in done.stdout


def pin_failures(stmt, current, check_image=None):
    """A statement is evidence about the image versions it was verified on.

    `verified_against` lists those references. When a service of the
    statement now runs another reference (a Renovate bump), the statement
    holds only if its `image_checks` (a file in the image contains a text)
    pass on the new reference; a statement without machine-checkable evidence
    needs a person to re-verify it and update `verified_against`.
    """
    failures = []
    for service in stmt.get("services") or []:
        image = current.get(service)
        if image is None:
            failures.append(f"{service} runs no scanned image")
        elif image not in (stmt.get("verified_against") or []):
            checks = stmt.get("image_checks") or []
            if not checks:
                failures.append(f"{service} now runs {image}, which this statement was not verified against; re-verify")
            elif check_image is None:
                failures.append(f"{service} now runs {image}; its image checks did not run (use --check-images)")
            elif not all(check_image(image, c["path"], c["contains"]) for c in checks):
                failures.append(f"{service} now runs {image}, and its image checks fail")
    return sorted(set(failures))


def statement_failures(stmt, deploy, dev, now, current=None, check_image=None):
    why = condition_failures(stmt, deploy, dev)
    if dt.date.fromisoformat(str(stmt["expires"])) < now:
        why.append(f"expired on {stmt['expires']}")
    if current is not None:
        why += pin_failures(stmt, current, check_image)
    return why


def current_images(target_list):
    return {s: t["image"] for t in target_list for s in t["services"]}


def validate_vex(doc, deploy, dev, now, current=None, check_image=None):
    """Return error strings for .vex/unused-components.json; empty means valid."""
    errors = []
    known = {f"deploy/{s}" for s in (deploy.get("services") or {})} | {f"dev/{s}" for s in ((dev or {}).get("services") or {})}
    known |= set(current or {})
    seen = set()
    for i, stmt in enumerate(doc.get("statements") or []):
        sid = stmt.get("id") or f"#{i}"
        if not stmt.get("id"):
            errors.append(f"{sid}: missing id")
        elif sid in seen:
            errors.append(f"{sid}: duplicate id")
        seen.add(sid)
        if not stmt.get("services"):
            errors.append(f"{sid}: no services")
        for service in stmt.get("services") or []:
            if service not in known:
                errors.append(f"{sid}: {service} is not a compose service")
        comp = stmt.get("component") or {}
        if not (comp.get("path") or comp.get("purl")):
            errors.append(f"{sid}: component needs a path or a purl")
        if stmt.get("justification") not in JUSTIFICATIONS:
            errors.append(f"{sid}: justification {stmt.get('justification')!r} is not an OpenVEX justification")
        if len((stmt.get("reason") or "").strip()) < REASON_MIN_CHARS:
            errors.append(f"{sid}: reason missing or shorter than {REASON_MIN_CHARS} characters")
        if not stmt.get("evidence"):
            errors.append(f"{sid}: no evidence")
        if not stmt.get("conditions"):
            errors.append(f"{sid}: needs at least one compose condition the validator can check")
        if not stmt.get("verified_against"):
            errors.append(f"{sid}: verified_against must name the image references the evidence was checked on")
        try:
            expires = dt.date.fromisoformat(str(stmt.get("expires")))
        except ValueError:
            errors.append(f"{sid}: expires must be YYYY-MM-DD")
        else:
            if expires < now:
                errors.append(f"{sid}: expired on {expires}")
            elif (expires - now).days > VEX_MAX_DAYS:
                errors.append(f"{sid}: expires more than {VEX_MAX_DAYS} days ahead")
        errors.extend(f"{sid}: {f}" for f in condition_failures(stmt, deploy, dev))
        if current is not None:
            errors.extend(f"{sid}: {f}" for f in pin_failures(stmt, current, check_image))
    return errors


VULN_ID = re.compile(r"\b(CVE-\d{4}-\d{4,}|GHSA(-[0-9a-z]{4}){3})\b", re.I)


def identifier_mentions(obj, where="$"):
    """JSON paths of strings that name a vulnerability identifier.

    These files are public and describe architecture ("component X is in
    image Y and never runs because Z"). An identifier in them would say which
    unpatched finding sits on which live image. The path is reported, not the
    identifier, because the validator's output is a public log too.
    """
    if isinstance(obj, str):
        return [f"{where} names a vulnerability identifier; describe the component, not the finding"] if VULN_ID.search(obj) else []
    if isinstance(obj, dict):
        return [m for k, v in obj.items() for m in identifier_mentions(k, where) + identifier_mentions(v, f"{where}.{k}")]
    if isinstance(obj, list):
        return [m for i, v in enumerate(obj) for m in identifier_mentions(v, f"{where}[{i}]")]
    return []


def validate_exposure(cfg, deploy, mentions, gpu=None):
    errors = []
    services = set(deploy.get("services") or {})
    services |= {o for _, o, kind, _ in image_refs({"deploy": deploy}) if kind == "env"}
    services |= {f"gpu/{s}" for s in ((gpu or {}).get("services") or {})}
    for section in ("content_consumers", "internal"):
        for service, reason in cfg[section].items():
            if service not in services:
                errors.append(f"image-exposure {section}: {service} is not a compose service")
            if len(reason.strip()) < 10:
                errors.append(f"image-exposure {section}: {service} needs a reason")
    for service in set(cfg["content_consumers"]) & set(cfg["internal"]):
        errors.append(f"image-exposure: {service} is both content-consuming and internal")
    for path in unlisted_route_sources(mentions, cfg):
        errors.append(
            f"{path} mentions reverse_proxy but is in neither route_sources nor not_route_sources "
            "of deploy/image-exposure.json"
        )
    return errors


def base_purl(purl):
    return re.split(r"[@?#]", purl or "", maxsplit=1)[0]


def vex_match(stmt, target, path, purl):
    if not set(target["services"]) <= set(stmt["services"]):
        return False
    comp = stmt["component"]
    if comp.get("path") and comp["path"] != path:
        return False
    purls = comp.get("purl") or []
    return not purls or base_purl(purl) in ([purls] if isinstance(purls, str) else purls)


def image_purl(image):
    name, _, _ = image.partition("@")
    name = re.sub(r":[^/:]+$", "", name)
    repo = name if "/" in name.split("/")[0] and "." in name.split("/")[0] else f"index.docker.io/{name}"
    return f"pkg:oci/{name.rsplit('/', 1)[-1]}?repository_url={repo.replace('/', '%2F')}"


# --- Feeds ------------------------------------------------------------------


def _get(url, urlopen, sleep):
    last = None
    for attempt in range(1, FEED_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode())
        except Exception as err:  # noqa: BLE001 - every failure is retried, then reported
            last = err
            if attempt < FEED_ATTEMPTS:
                sleep(10 * attempt)
    raise TriageError(f"{url.split('?')[0]} unreachable after {FEED_ATTEMPTS} attempts: {last}")


def epss_batches(ids):
    batch = []
    for cve in sorted(ids):
        if batch and (len(batch) == EPSS_BATCH or len(",".join([*batch, cve])) > EPSS_MAX_PARAM):
            yield batch
            batch = []
        batch.append(cve)
    if batch:
        yield batch


def fetch_kev(urlopen=urllib.request.urlopen, sleep=time.sleep):
    """The whole KEV catalog as {cveID: dateAdded}; one download per run.

    No cached copy and no partial answer: the KEV clause is the one that
    matters most, and a run that reports "nothing exploited" because CISA was
    unreachable is worse than a red run, which the failure notice reports.
    The catalog is public CISA data, so passing it between jobs reveals nothing.
    """
    kev = _get(KEV_URL, urlopen, sleep)
    rows = kev.get("vulnerabilities") if isinstance(kev, dict) else None
    if not rows or kev.get("count") != len(rows):
        raise TriageError("KEV feed answered without its catalog (vulnerabilities empty or count mismatch)")
    return {row["cveID"]: row["dateAdded"] for row in rows}


def fetch_epss(ids, urlopen=urllib.request.urlopen, sleep=time.sleep):
    """EPSS scores for the CVE ids among `ids`, in batches; raises rather than return less."""
    epss = {}
    for batch in epss_batches({i for i in ids if i.startswith("CVE-")}):
        page = _get(f"{EPSS_URL}?cve={','.join(batch)}&limit={len(batch)}", urlopen, sleep)
        if page.get("status") != "OK" or not isinstance(page.get("data"), list):
            raise TriageError(f"EPSS answered {page.get('status')!r} for a batch of {len(batch)} ids")
        epss.update({row["cve"]: float(row["epss"]) for row in page["data"]})
    return epss


def report_ids(report):
    return {c for r in report.get("Results") or [] for v in r.get("Vulnerabilities") or [] if (c := cve_of(v))}


# --- Triage -----------------------------------------------------------------


def _norm(name):
    return re.sub(r"[-_.]+", "-", name or "").lower()


def _eco(pkg):
    purl = (pkg.get("Identifier") or pkg.get("PkgIdentifier") or {}).get("PURL") or ""
    return purl.split("/", 1)[0]


def stale_sbom_layers(report):
    """Layers whose SBOM-sourced packages the installed inventory contradicts.

    Trivy reads every *.spdx, *.spdx.json, *.cdx and *.cdx.json in an image
    (pkg/fanal/analyzer/sbom/sbom.go, `requiredSuffixes`) and reports its
    components next to the packages it found installed. In the merged
    language result such a package has an Identifier.BOMRef and no FilePath;
    an installed package has a FilePath (its METADATA, package.json, ...).

    Trivy's JSON keeps no path to the SBOM file, so the layer is the unit.
    A layer's SBOM is not the image's inventory when both hold:
      - it lists a package the image has installed at another version, so it
        describes some other tree; and
      - the layer installed no package Trivy found itself, so the SBOM came in
        on its own (a copied source tree), not with the software it lists.
    On 2026-11-24 an upstream image shipped its project SBOM at
    tmp/project/sbom/sbom.cdx.json in a layer with no installed package; 144
    of its 270 components contradicted the installed versions and its 305
    findings were 302 of that day's alerts. Our build of that image now
    deletes the checkout, but nothing stops another upstream image from
    shipping the same kind of leftover, which is what this rule is for. A vendor SBOM is written by the
    step that installs what it lists (Bitnami's .spdx-<component>.spdx under
    opt/bitnami/, pip's pip/_vendor/bom.cdx.json next to pip's own
    dist-info), so its layer holds installed packages and it is kept even
    where a vendored copy differs from the installed one.

    "Installed" is computed across the whole image (every language result,
    keyed by package type and name), so an SBOM whose components Trivy spread
    over several results is judged once. The rule only decides findings that
    no keep clause would rescue: triage() never drops a known-exploited or
    high-EPSS finding through it, because a frozen binary with its own vendor
    SBOM in a layer that installed nothing would otherwise vanish.
    """
    packages = [pkg for r in report.get("Results") or [] if r.get("Class") == "lang-pkgs" for pkg in r.get("Packages") or []]
    installed, installed_layers = {}, set()
    for pkg in packages:
        if pkg.get("FilePath"):
            installed.setdefault((_eco(pkg), _norm(pkg.get("Name"))), set()).add(pkg.get("Version"))
            installed_layers.add((pkg.get("Layer") or {}).get("DiffID"))
    stale = set()
    for pkg in packages:
        if pkg.get("FilePath") or not (pkg.get("Identifier") or {}).get("BOMRef"):
            continue
        layer = (pkg.get("Layer") or {}).get("DiffID")
        versions = installed.get((_eco(pkg), _norm(pkg.get("Name"))))
        if versions and pkg.get("Version") not in versions and layer not in installed_layers:
            stale.add(layer)
    return stale


CVE_ID = re.compile(r"CVE-\d{4}-\d{4,}")


def cve_of(vuln):
    """The CVE a finding is known by in KEV and EPSS: its id, or the CVE alias
    of an advisory id (GHSA, a distribution advisory) from its vendor ids or
    references. None when there is none, so the finding cannot be joined."""
    vid = vuln["VulnerabilityID"]
    if vid.startswith("CVE-"):
        return vid
    for text in [*(vuln.get("VendorIDs") or []), *(vuln.get("References") or [])]:
        if m := CVE_ID.search(str(text)):
            return m.group(0)
    return None


def _age(since, now):
    try:
        return (now - dt.date.fromisoformat(str(since)[:10])).days
    except ValueError:
        return None


def keep_reasons(vuln, exposure, feeds, now):
    """Return (reasons, overdue). An empty reason list drops the finding.

    The scan no longer passes --ignore-unfixed, because a known-exploited
    finding matters most when there is no fix. A finding without a fix is
    kept only when it is in KEV, or crosses the EPSS threshold on an image
    that is not internal; the severity and age clauses apply to fixable ones.
    """
    vid = cve_of(vuln)
    critical = vuln.get("Severity") == "CRITICAL"
    fixable = bool(vuln.get("FixedVersion"))
    published = _age(vuln.get("PublishedDate"), now)
    reasons, overdue = [], False
    if vid in feeds["kev"]:
        reasons.append("kev")
        age = _age(feeds["kev"][vid], now)
        overdue = overdue or (fixable and (age is None or age > AGE_LIMIT_DAYS["kev"]))
    if feeds["epss"].get(vid, 0.0) >= EPSS_THRESHOLD and (fixable or exposure in EXPOSED_LIKE):
        reasons.append("epss")
    if not fixable:
        return reasons, overdue
    if critical and exposure in EXPOSED_LIKE:
        reasons.append("critical-exposed")
        overdue = overdue or (fixable and (published is None or published > AGE_LIMIT_DAYS["critical-exposed"]))
    if critical and exposure == "internal" and fixable and (published is None or published > AGE_LIMIT_DAYS["critical-internal"]):
        reasons.append("critical-overdue")
        overdue = True
    return reasons, overdue


# Packages that install only the kernel's userspace header files (paths under
# /usr/include, no binary, library or module). Scanners attribute every advisory
# of the `linux` source package to them. The vulnerable code is the kernel the
# host runs, never anything in the image, so even a KEV or high-EPSS row is
# noise here; the real control is the host kernel update (automatic reboot into
# patched kernels). Evidence per name: docs/runbooks/trivy-policy.md.
KERNEL_HEADER_PACKAGES = {"linux-libc-dev", "linux-headers", "kernel-headers"}


def triage(report, target, classes, feeds, vex, deploy, dev, now, current=None, check_image=None):
    exposure, exposure_reasons = image_exposure(target, classes)
    usable, skipped = [], []
    for stmt in (vex or {}).get("statements") or []:
        why = statement_failures(stmt, deploy, dev, now, current, check_image)
        if why:
            skipped.append({"id": stmt["id"], "why": why})
        else:
            usable.append(stmt)
    out = json.loads(json.dumps(report))
    kept, dropped, applied, vex_rows = [], {"kernel-headers": 0, "stale-sbom": 0, "vex": 0, "rule": 0}, set(), []
    total = 0
    stale, no_cve = stale_sbom_layers(out), 0
    for result in out.get("Results") or []:
        remaining = []
        for vuln in result.get("Vulnerabilities") or []:
            total += 1
            cve = cve_of(vuln)
            no_cve += cve is None
            ident = vuln.get("PkgIdentifier") or {}
            exploited = cve in feeds["kev"] or feeds["epss"].get(cve, 0.0) >= EPSS_THRESHOLD
            if result.get("Class") == "os-pkgs" and vuln.get("PkgName") in KERNEL_HEADER_PACKAGES:
                dropped["kernel-headers"] += 1
                continue
            in_stale = ident.get("BOMRef") and not vuln.get("PkgPath") and (vuln.get("Layer") or {}).get("DiffID") in stale
            if in_stale and result.get("Class") == "lang-pkgs" and not exploited:
                dropped["stale-sbom"] += 1
                continue
            path = vuln.get("PkgPath") or (result.get("Target") if result.get("Class") == "lang-pkgs" else None)
            stmt = next((s for s in usable if vex_match(s, target, path, ident.get("PURL"))), None)
            if stmt:
                dropped["vex"] += 1
                applied.add(stmt["id"])
                vex_rows.append((stmt, vuln, ident.get("PURL")))
                continue
            reasons, overdue = keep_reasons(vuln, exposure, feeds, now)
            if not reasons:
                dropped["rule"] += 1
                continue
            remaining.append(vuln)
            kept.append({
                "id": vuln["VulnerabilityID"],
                "package": vuln.get("PkgName"),
                "version": vuln.get("InstalledVersion"),
                "severity": vuln.get("Severity"),
                "path": path,
                "reasons": reasons,
                "overdue": overdue,
                "epss": feeds["epss"].get(cve),
            })
        if result.get("Vulnerabilities"):
            result["Vulnerabilities"] = remaining
    running = any(s.startswith("deploy/") for s in target["services"])
    summary = {
        "key": target["key"],
        "category": target["category"],
        "image": target["image"],
        "services": target["services"],
        "exposure": exposure,
        "exposure_reasons": exposure_reasons,
        "unknown_services": sorted(s for s in target["services"] if classes.get(s, ("unknown",))[0] == "unknown"),
        "total": total,
        "kept": kept,
        "dropped": dropped,
        "no_cve": no_cve,
        "vex_applied": sorted(applied),
        "vex_skipped": skipped,
        "kev_running": sorted({k["id"] for k in kept if "kev" in k["reasons"]}) if running else [],
    }
    return out, summary, openvex(target, vex_rows, now)


def openvex(target, rows, now):
    product = image_purl(target["image"])
    return {
        "@context": "https://openvex.dev/ns/v0.2.0",
        "@id": f"https://github.com/GetKlai/klai/vex/{target['category']}/{now.isoformat()}",
        "author": "scan-pinned-images.yml",
        "timestamp": f"{now.isoformat()}T00:00:00Z",
        "version": 1,
        "statements": [
            {
                "vulnerability": {"name": vuln["VulnerabilityID"]},
                "products": [{"@id": product, "subcomponents": [{"@id": purl}]}],
                "status": "not_affected",
                "justification": stmt["justification"],
                "impact_statement": stmt["reason"],
            }
            for stmt, vuln, purl in rows
        ],
    }


# --- Reporting and the pull-request gate -------------------------------------
#
# This repository is public, and so are its Actions logs, step summaries and
# artifacts (any signed-in user can download an artifact). Nothing printed or
# uploaded from here lets a reader tell a kept finding or WHICH image has one:
# no vulnerability id, no package and version, no per-image count. The detail
# goes only to the code-scanning tab as SARIF, which is not public. The weekly
# report is computed from that tab through the API (open alerts), so no
# per-image result ever has to travel between jobs.

NEEDS_ACTION = "the image scan left findings that need action; see the code-scanning tab"
GATE_NEEDS_ACTION = "the changed image pins add findings that need action; see the code-scanning tab for this pull request"


def _alert_cve(alert):
    rule = alert.get("rule") or {}
    m = CVE_ID.search(str(rule.get("id") or "")) or CVE_ID.search(str(rule.get("help") or ""))
    return m.group(0) if m else None


def tab_counts(alerts, target_list, kev, epss):
    """Whole-run counts of what the code-scanning tab now holds for this scan."""
    by_category = {t["category"]: t for t in target_list}
    counts = {"images": len(target_list), "images_with_findings": 0, "findings": 0,
              "kev": 0, "epss": 0, "other": 0, "kev_running": 0, "no_cve": 0}
    seen = set()
    for alert in alerts:
        inst = alert.get("most_recent_instance") or {}
        target = by_category.get(inst.get("category"))
        if alert.get("state") != "open" or inst.get("analysis_key") != SCAN_JOB_KEY or target is None:
            continue
        seen.add(target["category"])
        counts["findings"] += 1
        cve = _alert_cve(alert)
        counts["no_cve"] += cve is None
        if cve in kev:
            counts["kev"] += 1
            counts["kev_running"] += any(s.startswith("deploy/") for s in target["services"])
        elif epss.get(cve, 0.0) >= EPSS_THRESHOLD:
            counts["epss"] += 1
        else:
            counts["other"] += 1
    counts["images_with_findings"] = len(seen)
    return counts


def report_markdown(counts, notes=()):
    lines = [
        f"Scanned {counts['images']} images; {counts['images_with_findings']} have at least one finding "
        f"on the code-scanning tab, {counts['findings']} findings in all.",
        "",
        "| On the tab because | Count |",
        "|---|---:|",
        f"| in the CISA KEV catalog | {counts['kev']} |",
        f"| EPSS at least {EPSS_THRESHOLD} | {counts['epss']} |",
        f"| critical on an exposed, reachable or content-consuming image, or overdue | {counts['other']} |",
        "",
        f"Findings without a CVE id (not joined to KEV or EPSS): {counts['no_cve']}.",
    ]
    return "\n".join(lines + ([""] + sorted(set(notes)) if notes else [])) + "\n"


def architecture_notes(target_list, classes, vex, deploy, dev, now, current):
    """Unclassified services and VEX statements that do not hold: architecture, no findings."""
    notes = set()
    for t in target_list:
        for s in t["services"]:
            if classes.get(s, ("unknown",))[0] == "unknown":
                notes.add(f"- Unclassified, treated as exposed: {s}")
    for stmt in (vex or {}).get("statements") or []:
        why = statement_failures(stmt, deploy, dev, now, current)
        if why:
            notes.add(f"- VEX statement `{stmt['id']}` not applied unless its image checks pass: {'; '.join(why)}")
    return notes


def changed_targets(base, head):
    base_images = {(t["image"], t.get("context", "")) for t in base}
    changed = [t for t in head if (t["image"], t.get("context", "")) not in base_images]
    services = {s for t in changed for s in t["services"]}
    counterparts = [t for t in base if services & set(t["services"])]
    return {"head": changed, "base": counterparts}


def introduced_kev(base_summaries, head_summaries, main_ids=None):
    """KEV findings on changed head pins that neither the old pin's scan nor
    the default branch's open alerts for those services already had.

    `main_ids` maps a category to the rule ids open on main. It covers a base
    tag that can no longer be pulled, so a bump that fixes something is not
    blocked because the image it replaces disappeared upstream.
    """
    out = []
    for head in head_summaries:
        if head["exposure"] not in EXPOSED_LIKE:
            continue
        related = [b for b in base_summaries if set(b["services"]) & set(head["services"])]
        before = {row["id"] for b in related for row in b["kept"]}
        for category in {head["category"], *(b["category"] for b in related)}:
            before |= set((main_ids or {}).get(category, ()))
        for row in head["kept"]:
            if "kev" in row["reasons"] and row["id"] not in before:
                out.append((row["id"], head["key"]))
    return sorted(set(out))


# --- CLI --------------------------------------------------------------------


STACK_FILES = ("deploy/docker-compose.yml", "docker-compose.dev.yml", "deploy/docker-compose.gpu.yml")
SETTINGS_FILE = "klai-portal/backend/app/core/config.py"


def _load_all(directory, suffix):
    return [load(p) for p in sorted(Path(directory).rglob(f"*{suffix}"))]


def _composes(a):
    out = {"deploy": load(a.deploy), "dev": load(a.dev)}
    if getattr(a, "gpu", None):
        out["gpu"] = load(a.gpu)
    return out


def _settings(a):
    return Path(a.settings).read_text() if getattr(a, "settings", None) and Path(a.settings).exists() else ""


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def compose_args(c, crawl=False):
        c.add_argument("--deploy", required=True)
        c.add_argument("--dev", required=True)
        c.add_argument("--gpu")
        c.add_argument("--settings", default=SETTINGS_FILE)
        if crawl:
            c.add_argument("--context-root", default=".", help="where the build contexts (deploy/crawl4ai) are checked out")

    c = sub.add_parser("targets")
    compose_args(c, crawl=True)
    c = sub.add_parser("stale")
    c.add_argument("--alerts", required=True, help="gh api --paginate --slurp output")
    c.add_argument("--targets", required=True)
    c = sub.add_parser("kev")
    c.add_argument("--out", required=True)
    c = sub.add_parser("triage")
    for name in ("scan", "targets", "key", "kev", "out-dir"):
        c.add_argument(f"--{name}", required=True)
    compose_args(c)
    c.add_argument("--repo", default=".")
    c.add_argument("--exposure", default="deploy/image-exposure.json")
    c.add_argument("--vex", default=".vex/unused-components.json")
    c.add_argument("--check-images", action="store_true")
    c = sub.add_parser("report")
    c.add_argument("--alerts", required=True, help="gh api --paginate --slurp output")
    c.add_argument("--targets", required=True)
    c.add_argument("--kev", required=True)
    compose_args(c)
    c.add_argument("--repo", default=".")
    c.add_argument("--exposure", default="deploy/image-exposure.json")
    c.add_argument("--vex", default=".vex/unused-components.json")
    c.add_argument("--markdown")
    c = sub.add_parser("changed")
    c.add_argument("--base", required=True)
    c.add_argument("--head", required=True)
    c = sub.add_parser("gate")
    c.add_argument("--base", required=True)
    c.add_argument("--head", required=True)
    c.add_argument("--main-alerts", help="gh api --paginate --slurp output for the default branch")
    c = sub.add_parser("validate")
    compose_args(c, crawl=True)
    c.add_argument("--repo", default=".")
    c.add_argument("--exposure", default="deploy/image-exposure.json")
    c.add_argument("--vex", default=".vex/unused-components.json")
    c.add_argument("--renovate", default="renovate.json5")
    c.add_argument("--check-images", action="store_true")
    a = p.parse_args(argv)
    try:
        return COMMANDS[a.cmd](a)
    except TriageError as err:
        print(f"::error::{err}")
        return 2


def _slurp(path):
    pages = load(path)
    return [x for page in pages for x in page] if pages and isinstance(pages[0], list) else pages


def _cmd_targets(a):
    print(json.dumps(targets(_composes(a), _settings(a), a.context_root)))
    return 0


def _cmd_stale(a):
    current = {t["category"] for t in load(a.targets)}
    entries = [{"key": c, "category": c, "image": "", "services": [], "stale": True} for c in stale_categories(_slurp(a.alerts), current)]
    print(json.dumps(entries))
    return 0


def _cmd_kev(a):
    kev = fetch_kev()
    write(a.out, kev)
    print(f"KEV catalog: {len(kev)} entries")
    return 0


def _route_texts(repo, cfg):
    return {path: (Path(repo) / path).read_text() for path in cfg["route_sources"]}


def _classes(a, composes):
    cfg = load(a.exposure)
    unlisted = unlisted_route_sources(find_route_mentions(a.repo), cfg)
    if unlisted:
        raise TriageError(f"unclassified route sources: {', '.join(unlisted)} (run the validate subcommand)")
    return classify(composes["deploy"], composes["dev"], cfg, _route_texts(a.repo, cfg), composes.get("gpu"))


def _cmd_triage(a):
    composes = _composes(a)
    classes = _classes(a, composes)
    target_list = load(a.targets)
    target = next((t for t in target_list if t["key"] == a.key), None)
    if target is None:
        raise TriageError(f"{a.key} is not a scan target")
    vex = load(a.vex) if Path(a.vex).exists() else {}
    report = load(a.scan)
    feeds = {"kev": load(a.kev), "epss": fetch_epss(report_ids(report))}
    out, summary, vexdoc = triage(report, target, classes, feeds, vex, composes["deploy"], composes["dev"], today(),
                                  current_images(target_list), image_file_contains if a.check_images else None)
    # The detail stays on the runner; nothing of it is printed or uploaded.
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    write(d / f"{a.key}.triaged.json", out)
    write(d / f"{a.key}.summary.json", summary)
    write(d / f"{a.key}.openvex.json", vexdoc)
    return 0


def _cmd_report(a):
    composes = _composes(a)
    target_list = load(a.targets)
    alerts, kev = _slurp(a.alerts), load(a.kev)
    open_cves = {c for x in alerts if x.get("state") == "open" and (c := _alert_cve(x))}
    counts = tab_counts(alerts, target_list, kev, fetch_epss(open_cves))
    vex = load(a.vex) if Path(a.vex).exists() else {}
    notes = architecture_notes(target_list, _classes(a, composes), vex, composes["deploy"], composes["dev"], today(),
                               current_images(target_list))
    markdown = report_markdown(counts, notes)
    print(markdown)
    if a.markdown:
        Path(a.markdown).write_text(markdown)
    if counts["kev_running"]:
        print(f"::error::{NEEDS_ACTION}")
        return 1
    return 0


def _cmd_changed(a):
    print(json.dumps(changed_targets(load(a.base), load(a.head))))
    return 0


def _cmd_gate(a):
    main_ids = {}
    for alert in _slurp(a.main_alerts) if a.main_alerts else []:
        inst = alert.get("most_recent_instance") or {}
        if alert.get("state") == "open":
            main_ids.setdefault(inst.get("category"), set()).add((alert.get("rule") or {}).get("id"))
    found = introduced_kev(_load_all(a.base, ".summary.json"), _load_all(a.head, ".summary.json"), main_ids)
    if found:
        print(f"::error::{GATE_NEEDS_ACTION}")
        return 1
    print("No changed pin adds a known-exploited finding on an image that is not internal.")
    return 0


def _cmd_validate(a):
    composes = _composes(a)
    deploy, dev = composes["deploy"], composes["dev"]
    vex, cfg = load(a.vex), load(a.exposure)
    settings = _settings(a)
    target_list = targets(composes, settings, a.context_root)
    errors = validate_vex(vex, deploy, dev, today(), current_images(target_list), image_file_contains if a.check_images else None)
    errors += validate_exposure(cfg, deploy, find_route_mentions(a.repo), composes.get("gpu"))
    errors += [f"{a.vex}: {e}" for e in identifier_mentions(vex)]
    errors += [f"{a.exposure}: {e}" for e in identifier_mentions(cfg)]
    recognised = {image for *_, image in image_refs(composes, settings)}
    pin_files = {path: (Path(a.repo) / path).read_text() for path in (*STACK_FILES, SETTINGS_FILE) if (Path(a.repo) / path).exists()}
    errors += unrecognised_renovate_pins(load_json5((Path(a.repo) / a.renovate).read_text()), pin_files, recognised)
    if not errors:
        classify(deploy, dev, cfg, _route_texts(a.repo, cfg), composes.get("gpu"))
    for message in errors:
        print(f"::error::{message}")
    if not errors:
        print(f"[validate-vex] {a.vex}: {len(vex.get('statements') or [])} statements OK; {a.exposure} OK; "
              f"{len(target_list)} scan targets, every Renovate image pin among them")
    return 1 if errors else 0


COMMANDS = {
    "targets": _cmd_targets,
    "stale": _cmd_stale,
    "kev": _cmd_kev,
    "triage": _cmd_triage,
    "report": _cmd_report,
    "changed": _cmd_changed,
    "gate": _cmd_gate,
    "validate": _cmd_validate,
}

if __name__ == "__main__":
    sys.exit(main())
