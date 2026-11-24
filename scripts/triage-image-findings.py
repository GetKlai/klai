#!/usr/bin/env python3
"""Reduce the Trivy findings on third-party images to the ones someone acts on.

The weekly scan of every external image pinned in compose produced 1,275 open
code-scanning alerts on 2026-11-24 (442 distinct ids). A list that long is not
acted on. This script decides, per image, which findings go to the
code-scanning tab and why; the full Trivy result stays a workflow artifact.

A finding is DROPPED when
  - its package comes from an SBOM file inside the image that the image's own
    installed packages contradict (see stale_sbom_layers), or
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
  feeds     Trivy results -> KEV and EPSS for their ids (fails on an outage)
  triage    one Trivy result -> triaged Trivy JSON, summary, OpenVEX
  summary   summaries -> Markdown table, report JSON; --fail-on-kev
  changed   base and head targets -> pins a pull request changes
  gate      base and head summaries -> fail on a KEV finding the change adds
  validate  .vex/unused-components.json and deploy/image-exposure.json

Standard library only. Tests: python3 -m unittest scripts/tests/test_triage_image_findings.py
"""

from __future__ import annotations

import argparse
import datetime as dt
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

RANK = {"internal": 0, "content": 1, "exposed": 2, "unknown": 3}
EXPOSED_LIKE = {"content", "exposed", "unknown"}

# The upload job's analysis key; GitHub keeps one alert set per tool, key and
# category, so stale categories are only those of this job.
SCAN_JOB_KEY = ".github/workflows/scan-pinned-images.yml:scan"

# Same filter the workflow's jq used before this script existed.
EXCLUDED_IMAGE = re.compile(r"^(ghcr\.io/getklai/[^@]+$|[^:]+:klai$|[^:]+:local$|[^:]+:.*-local-[0-9]{6}-[0-9]{4}$)")
CRAWL4AI_IMAGE = re.compile(r"^ghcr\.io/getklai/crawl4ai:")
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


def targets(deploy, dev, crawl4ai_base):
    groups = {}
    for source, compose in (("deploy", deploy), ("dev", dev)):
        for service, spec in ((compose or {}).get("services") or {}).items():
            image = (spec or {}).get("image")
            if not image:
                continue
            if CRAWL4AI_IMAGE.search(image):
                # Built on the server and never pushed; its Dockerfile adds no
                # packages, so the FROM image carries the same package set.
                image = crawl4ai_base
            if EXCLUDED_IMAGE.search(image):
                continue
            groups.setdefault(image, []).append((source, service))
    out = []
    for image, members in groups.items():
        key = "__".join(sorted(f"{s}_{n}" for s, n in members))
        out.append({
            "key": key,
            "services": sorted(f"{s}/{n}" for s, n in members),
            "image": image,
            "category": "pinned-service-" + re.sub(r"[^A-Za-z0-9_.-]", "_", key),
        })
    return sorted(out, key=lambda t: t["key"])


def crawl4ai_base(dockerfile_text):
    for line in dockerfile_text.splitlines():
        if line.startswith("FROM "):
            return line[5:]
    return ""


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


def classify(deploy, dev, cfg, route_texts):
    """Return {"deploy/<svc>" | "dev/<svc>": (class, reason)}."""
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
            image = env_of(services.get(runtime["service"])).get(runtime["env"], "")
            if not image or "${" in image:
                raise TriageError(
                    f"{path} routes to {host!r}, whose image comes from {runtime['env']} of "
                    f"{runtime['service']}, and compose does not pin that value"
                )
            matched = [s for s, spec in services.items() if (spec or {}).get("image") == image]
            if not matched:
                raise TriageError(
                    f"{path} routes to containers running {image}, which no compose service "
                    "runs, so no scan covers the image tenants are served from"
                )
            for service in matched:
                exposed.setdefault(service, f"{runtime['reason']} ({path})")
    for service, spec in services.items():
        if (spec or {}).get("network_mode") == "host":
            exposed.setdefault(service, "runs in the host network namespace")
        for port in (spec or {}).get("ports") or []:
            if public_port(port):
                exposed.setdefault(service, f"publishes {port} on every host interface")
    classes = {}
    for service in services:
        if service in exposed:
            classes[f"deploy/{service}"] = ("exposed", exposed[service])
        elif service in cfg["content_consumers"]:
            classes[f"deploy/{service}"] = ("content", cfg["content_consumers"][service])
        elif service in cfg["internal"]:
            classes[f"deploy/{service}"] = ("internal", cfg["internal"][service])
        else:
            classes[f"deploy/{service}"] = ("unknown", "not classified in deploy/image-exposure.json, treated as exposed")
    for service in (dev or {}).get("services") or {}:
        classes[f"dev/{service}"] = ("internal", "docker-compose.dev.yml runs on developer machines only")
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
    return failures


def validate_vex(doc, deploy, dev, now):
    """Return error strings for .vex/unused-components.json; empty means valid."""
    errors = []
    known = {f"deploy/{s}" for s in (deploy.get("services") or {})} | {f"dev/{s}" for s in ((dev or {}).get("services") or {})}
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
    return errors


def validate_exposure(cfg, deploy, mentions):
    errors = []
    services = set(deploy.get("services") or {})
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


def fetch_feeds(ids, urlopen=urllib.request.urlopen, sleep=time.sleep):
    """KEV and EPSS for `ids`. Raises TriageError rather than return less.

    No cached copy: the KEV clause is the one that matters most, and a run
    that reports "nothing exploited" because CISA was unreachable is worse than
    a red run, which the failure notice turns into an issue.
    """
    kev = _get(KEV_URL, urlopen, sleep)
    rows = kev.get("vulnerabilities") if isinstance(kev, dict) else None
    if not rows or kev.get("count") != len(rows):
        raise TriageError("KEV feed answered without its catalog (vulnerabilities empty or count mismatch)")
    cves = sorted(i for i in ids if i.startswith("CVE-"))
    epss = {}
    for batch in epss_batches(cves):
        page = _get(f"{EPSS_URL}?cve={','.join(batch)}&limit={len(batch)}", urlopen, sleep)
        if page.get("status") != "OK" or not isinstance(page.get("data"), list):
            raise TriageError(f"EPSS answered {page.get('status')!r} for a batch of {len(batch)} ids")
        epss.update({row["cve"]: float(row["epss"]) for row in page["data"]})
    return {
        "kev": {row["cveID"]: row["dateAdded"] for row in rows},
        "kev_catalog_version": kev.get("catalogVersion"),
        "epss": epss,
        "epss_requested": len(cves),
    }


def report_ids(report):
    return {v["VulnerabilityID"] for r in report.get("Results") or [] for v in r.get("Vulnerabilities") or []}


# --- Triage -----------------------------------------------------------------


def _norm(name):
    return re.sub(r"[-_.]+", "-", name or "").lower()


def stale_sbom_layers(result):
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
    findings were 302 of that day's alerts. A vendor SBOM is written by the
    step that installs what it lists (Bitnami's .spdx-<component>.spdx under
    opt/bitnami/, pip's pip/_vendor/bom.cdx.json next to pip's own
    dist-info), so its layer holds installed packages and it is kept even
    where a vendored copy differs from the installed one.
    """
    installed, installed_layers = {}, set()
    for pkg in result.get("Packages") or []:
        if pkg.get("FilePath"):
            installed.setdefault(_norm(pkg.get("Name")), set()).add(pkg.get("Version"))
            installed_layers.add((pkg.get("Layer") or {}).get("DiffID"))
    stale = set()
    for pkg in result.get("Packages") or []:
        if pkg.get("FilePath") or not (pkg.get("Identifier") or {}).get("BOMRef"):
            continue
        layer = (pkg.get("Layer") or {}).get("DiffID")
        versions = installed.get(_norm(pkg.get("Name")))
        if versions and pkg.get("Version") not in versions and layer not in installed_layers:
            stale.add(layer)
    return stale


def _age(since, now):
    try:
        return (now - dt.date.fromisoformat(str(since)[:10])).days
    except ValueError:
        return None


def keep_reasons(vuln, exposure, feeds, now):
    """Return (reasons, overdue). An empty reason list drops the finding."""
    vid = vuln["VulnerabilityID"]
    critical = vuln.get("Severity") == "CRITICAL"
    fixable = bool(vuln.get("FixedVersion"))
    published = _age(vuln.get("PublishedDate"), now)
    reasons, overdue = [], False
    if vid in feeds["kev"]:
        reasons.append("kev")
        age = _age(feeds["kev"][vid], now)
        overdue = overdue or (fixable and (age is None or age > AGE_LIMIT_DAYS["kev"]))
    if feeds["epss"].get(vid, 0.0) >= EPSS_THRESHOLD:
        reasons.append("epss")
    if critical and exposure in EXPOSED_LIKE:
        reasons.append("critical-exposed")
        overdue = overdue or (fixable and (published is None or published > AGE_LIMIT_DAYS["critical-exposed"]))
    if critical and exposure == "internal" and fixable and (published is None or published > AGE_LIMIT_DAYS["critical-internal"]):
        reasons.append("critical-overdue")
        overdue = True
    return reasons, overdue


def triage(report, target, classes, feeds, vex, deploy, dev, now):
    exposure, exposure_reasons = image_exposure(target, classes)
    usable, skipped = [], []
    for stmt in (vex or {}).get("statements") or []:
        why = condition_failures(stmt, deploy, dev)
        if dt.date.fromisoformat(str(stmt["expires"])) < now:
            why.append(f"expired on {stmt['expires']}")
        if why:
            skipped.append({"id": stmt["id"], "why": why})
        else:
            usable.append(stmt)
    out = json.loads(json.dumps(report))
    kept, dropped, applied, vex_rows = [], {"stale-sbom": 0, "vex": 0, "rule": 0}, set(), []
    total = 0
    for result in out.get("Results") or []:
        stale = stale_sbom_layers(result) if result.get("Class") == "lang-pkgs" else set()
        remaining = []
        for vuln in result.get("Vulnerabilities") or []:
            total += 1
            ident = vuln.get("PkgIdentifier") or {}
            if ident.get("BOMRef") and not vuln.get("PkgPath") and (vuln.get("Layer") or {}).get("DiffID") in stale:
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
                "epss": feeds["epss"].get(vuln["VulnerabilityID"]),
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


def summary_markdown(summaries):
    lines = [
        "| Image | Exposure | Total | Kept | Why kept | Dropped: stale SBOM / VEX / rule |",
        "|---|---|---:|---:|---|---|",
    ]
    for s in sorted(summaries, key=lambda s: (-len(s["kept"]), s["key"])):
        why = {}
        for row in s["kept"]:
            for reason in row["reasons"]:
                why[reason] = why.get(reason, 0) + 1
        why_text = ", ".join(f"{k} {v}" for k, v in sorted(why.items())) or "-"
        d = s["dropped"]
        lines.append(
            f"| `{s['image']}` | {s['exposure']} | {s['total']} | {len(s['kept'])} | {why_text} "
            f"| {d['stale-sbom']} / {d['vex']} / {d['rule']} |"
        )
    total = sum(s["total"] for s in summaries)
    kept = sum(len(s["kept"]) for s in summaries)
    lines.append(f"| **all {len(summaries)} images** | | {total} | {kept} | | |")
    notes = []
    for s in summaries:
        if s["unknown_services"]:
            notes.append(f"- Unclassified, treated as exposed: {', '.join(s['unknown_services'])}")
        for skipped in s["vex_skipped"]:
            notes.append(f"- VEX statement `{skipped['id']}` not applied: {'; '.join(skipped['why'])}")
    return "\n".join(lines + ([""] + sorted(set(notes)) if notes else [])) + "\n"


def kev_report(summaries):
    return sorted({(cve, s["image"]) for s in summaries for cve in s["kev_running"]})


def changed_targets(base, head):
    base_images = {t["image"] for t in base}
    changed = [t for t in head if t["image"] not in base_images]
    services = {s for t in changed for s in t["services"]}
    counterparts = [t for t in base if services & set(t["services"])]
    return {"head": changed, "base": counterparts}


def introduced_kev(base_summaries, head_summaries):
    out = []
    for head in head_summaries:
        if head["exposure"] not in EXPOSED_LIKE:
            continue
        before = {
            (row["id"], row["package"])
            for base in base_summaries
            if set(base["services"]) & set(head["services"])
            for row in base["kept"]
        }
        for row in head["kept"]:
            if "kev" in row["reasons"] and (row["id"], row["package"]) not in before:
                out.append((row["id"], row["package"], head["image"], head["exposure"]))
    return sorted(set(out))


# --- CLI --------------------------------------------------------------------


def _summaries(directory):
    return [load(p) for p in sorted(Path(directory).rglob("*.summary.json"))]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("targets")
    c.add_argument("--deploy", required=True)
    c.add_argument("--dev", required=True)
    c.add_argument("--crawl4ai-dockerfile", required=True)
    c = sub.add_parser("stale")
    c.add_argument("--alerts", required=True, help="gh api --paginate --slurp output")
    c.add_argument("--targets", required=True)
    c = sub.add_parser("feeds")
    c.add_argument("--scans", required=True, help="directory of Trivy JSON results")
    c.add_argument("--out", required=True)
    c = sub.add_parser("triage")
    for name in ("scan", "targets", "key", "feeds", "deploy", "dev", "out-dir"):
        c.add_argument(f"--{name}", required=True)
    c.add_argument("--repo", default=".")
    c.add_argument("--exposure", default="deploy/image-exposure.json")
    c.add_argument("--vex", default=".vex/unused-components.json")
    c = sub.add_parser("summary")
    c.add_argument("--summaries", required=True)
    c.add_argument("--report")
    c.add_argument("--markdown")
    c.add_argument("--fail-on-kev", action="store_true")
    c = sub.add_parser("changed")
    c.add_argument("--base", required=True)
    c.add_argument("--head", required=True)
    c = sub.add_parser("gate")
    c.add_argument("--base", required=True)
    c.add_argument("--head", required=True)
    c = sub.add_parser("validate")
    c.add_argument("--deploy", required=True)
    c.add_argument("--dev", required=True)
    c.add_argument("--repo", default=".")
    c.add_argument("--exposure", default="deploy/image-exposure.json")
    c.add_argument("--vex", default=".vex/unused-components.json")
    a = p.parse_args(argv)
    try:
        return COMMANDS[a.cmd](a)
    except TriageError as err:
        print(f"::error::{err}")
        return 2


def _cmd_targets(a):
    base = crawl4ai_base(Path(a.crawl4ai_dockerfile).read_text())
    print(json.dumps(targets(load(a.deploy), load(a.dev), base)))
    return 0


def _cmd_stale(a):
    pages = load(a.alerts)
    alerts = [x for page in pages for x in page] if pages and isinstance(pages[0], list) else pages
    current = {t["category"] for t in load(a.targets)}
    entries = [{"key": c, "category": c, "image": "", "services": [], "stale": True} for c in stale_categories(alerts, current)]
    print(json.dumps(entries))
    return 0


def _cmd_feeds(a):
    ids = set()
    for path in sorted(Path(a.scans).rglob("*.json")):
        ids |= report_ids(load(path))
    feeds = fetch_feeds(ids)
    write(a.out, feeds)
    print(
        f"KEV catalog {feeds['kev_catalog_version']}: {len(feeds['kev'])} entries; "
        f"EPSS: {len(feeds['epss'])} of {feeds['epss_requested']} CVE ids scored"
    )
    return 0


def _route_texts(repo, cfg):
    return {path: (Path(repo) / path).read_text() for path in cfg["route_sources"]}


def _cmd_triage(a):
    cfg, deploy, dev = load(a.exposure), load(a.deploy), load(a.dev)
    unlisted = unlisted_route_sources(find_route_mentions(a.repo), cfg)
    if unlisted:
        raise TriageError(f"unclassified route sources: {', '.join(unlisted)} (run the validate subcommand)")
    classes = classify(deploy, dev, cfg, _route_texts(a.repo, cfg))
    target = next((t for t in load(a.targets) if t["key"] == a.key), None)
    if target is None:
        raise TriageError(f"{a.key} is not a scan target")
    vex = load(a.vex) if Path(a.vex).exists() else {}
    out, summary, vexdoc = triage(load(a.scan), target, classes, load(a.feeds), vex, deploy, dev, today())
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    write(d / f"{a.key}.triaged.json", out)
    write(d / f"{a.key}.summary.json", summary)
    write(d / f"{a.key}.openvex.json", vexdoc)
    print(f"{a.key}: {summary['total']} findings, {len(summary['kept'])} kept, exposure {summary['exposure']}")
    for service in summary["unknown_services"]:
        print(f"::warning::{service} is not classified in deploy/image-exposure.json and is treated as exposed")
    for skipped in summary["vex_skipped"]:
        print(f"::warning::VEX statement {skipped['id']} not applied: {'; '.join(skipped['why'])}")
    return 0


def _cmd_summary(a):
    summaries = _summaries(a.summaries)
    markdown = summary_markdown(summaries)
    print(markdown)
    if a.markdown:
        Path(a.markdown).write_text(markdown)
    kev = kev_report(summaries)
    if a.report:
        write(a.report, {"kev_running": [{"id": i, "image": img} for i, img in kev]})
    if kev and a.fail_on_kev:
        for cve, image in kev:
            print(f"::error::{cve} is in the CISA KEV catalog and present on {image}")
        return 1
    return 0


def _cmd_changed(a):
    print(json.dumps(changed_targets(load(a.base), load(a.head))))
    return 0


def _cmd_gate(a):
    found = introduced_kev(_summaries(a.base), _summaries(a.head))
    for cve, pkg, image, exposure in found:
        print(f"::error::{image} ({exposure}) adds {cve} in {pkg}, which is in the CISA KEV catalog")
    if not found:
        print("No changed pin adds a KEV finding on an exposed or content-consuming image.")
    return 1 if found else 0


def _cmd_validate(a):
    deploy, dev = load(a.deploy), load(a.dev)
    errors = validate_vex(load(a.vex), deploy, dev, today())
    cfg = load(a.exposure)
    errors += validate_exposure(cfg, deploy, find_route_mentions(a.repo))
    if not errors:
        classify(deploy, dev, cfg, _route_texts(a.repo, cfg))
    for message in errors:
        print(f"::error::{message}")
    if not errors:
        print(f"[validate-vex] {a.vex}: {len(load(a.vex).get('statements') or [])} statements OK; {a.exposure} OK")
    return 1 if errors else 0


COMMANDS = {
    "targets": _cmd_targets,
    "stale": _cmd_stale,
    "feeds": _cmd_feeds,
    "triage": _cmd_triage,
    "summary": _cmd_summary,
    "changed": _cmd_changed,
    "gate": _cmd_gate,
    "validate": _cmd_validate,
}

if __name__ == "__main__":
    sys.exit(main())
