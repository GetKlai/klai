"""Tests for scripts/triage-image-findings.py.

Every Trivy report, compose file and feed below is built in memory from the
helpers in this file (synthetic ids such as CVE-2099-0001, example/... image
names); no scan output is stored in the repository. The replay test draws a
report set from a seeded generator whose per-image counts follow the shape of
the 2026-11-24 alert list (counts only, no values from it).

Run: python3 -m unittest scripts/tests/test_triage_image_findings.py
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
import json
import random
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("triage", ROOT / "scripts" / "triage-image-findings.py")
T = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(T)

NOW = dt.date(2099, 6, 1)
NO_FEEDS = {"kev": {}, "epss": {}}


def days_ago(n):
    return (NOW - dt.timedelta(days=n)).isoformat() + "T00:00:00Z"


def vuln(vid, severity="HIGH", pkg="libexample", fixed="1.1", published=10, path=None, purl=None, layer="L1", bomref=False):
    v = {
        "VulnerabilityID": vid,
        "PkgName": pkg,
        "InstalledVersion": "1.0",
        "FixedVersion": fixed,
        "Severity": severity,
        "PublishedDate": days_ago(published) if published is not None else None,
        "PkgIdentifier": {"PURL": purl or f"pkg:deb/debian/{pkg}@1.0?arch=amd64"},
        "Layer": {"DiffID": layer},
    }
    if path:
        v["PkgPath"] = path
    if bomref:
        v["PkgIdentifier"]["BOMRef"] = v["PkgIdentifier"]["PURL"]
    return v


def report(*vulns, results=None):
    return {
        "ArtifactName": "example/app:1.0",
        "Results": results or [{"Target": "example/app:1.0 (debian 99)", "Class": "os-pkgs", "Vulnerabilities": list(vulns)}],
    }


def target(*services, image="example/app:1.0"):
    key = "__".join(s.replace("/", "_") for s in services)
    return {"key": key, "services": list(services), "image": image, "category": f"pinned-service-{key}"}


def classes(**per):
    return {f"deploy/{k}": (v, "test") for k, v in per.items()}


def run(rep, tgt, cls, feeds=NO_FEEDS, vex=None, deploy=None, dev=None, now=NOW):
    return T.triage(rep, tgt, cls, feeds, vex or {}, deploy or {"services": {}}, dev or {"services": {}}, now)


def kept_ids(summary):
    return [k["id"] for k in summary["kept"]]


class KeepRule(unittest.TestCase):
    def test_kev_is_kept_on_an_internal_image(self):
        feeds = {"kev": {"CVE-2099-0001": "2099-05-30"}, "epss": {}}
        _, s, _ = run(report(vuln("CVE-2099-0001")), target("deploy/db"), classes(db="internal"), feeds)
        self.assertEqual(s["kept"][0]["reasons"], ["kev"])

    def test_epss_at_the_threshold_is_kept_and_just_below_is_dropped(self):
        rep = report(vuln("CVE-2099-0002"), vuln("CVE-2099-0003"))
        feeds = {"kev": {}, "epss": {"CVE-2099-0002": T.EPSS_THRESHOLD, "CVE-2099-0003": T.EPSS_THRESHOLD - 0.001}}
        _, s, _ = run(rep, target("deploy/db"), classes(db="internal"), feeds)
        self.assertEqual(kept_ids(s), ["CVE-2099-0002"])
        self.assertEqual(s["dropped"]["rule"], 1)

    def test_critical_is_kept_on_exposed_and_content_images_only(self):
        rep = report(vuln("CVE-2099-0004", "CRITICAL"))
        for cls, kept in (("exposed", True), ("content", True), ("internal", False)):
            _, s, _ = run(rep, target("deploy/svc"), classes(svc=cls))
            self.assertEqual(bool(s["kept"]), kept, cls)

    def test_high_on_an_exposed_image_without_kev_or_epss_is_dropped(self):
        _, s, _ = run(report(vuln("CVE-2099-0005", "HIGH")), target("deploy/web"), classes(web="exposed"))
        self.assertEqual(s["kept"], [])


class AgeLimits(unittest.TestCase):
    def test_internal_critical_is_kept_only_after_its_limit(self):
        limit = T.AGE_LIMIT_DAYS["critical-internal"]
        rep = report(vuln("CVE-2099-0010", "CRITICAL", published=limit), vuln("CVE-2099-0011", "CRITICAL", published=limit + 1))
        _, s, _ = run(rep, target("deploy/db"), classes(db="internal"))
        self.assertEqual(kept_ids(s), ["CVE-2099-0011"])
        self.assertEqual(s["kept"][0]["reasons"], ["critical-overdue"])

    def test_internal_critical_without_a_fix_never_becomes_overdue(self):
        rep = report(vuln("CVE-2099-0012", "CRITICAL", fixed="", published=400))
        _, s, _ = run(rep, target("deploy/db"), classes(db="internal"))
        self.assertEqual(s["kept"], [])

    def test_exposed_critical_turns_overdue_after_its_limit(self):
        limit = T.AGE_LIMIT_DAYS["critical-exposed"]
        rep = report(vuln("CVE-2099-0013", "CRITICAL", published=limit), vuln("CVE-2099-0014", "CRITICAL", published=limit + 1))
        _, s, _ = run(rep, target("deploy/web"), classes(web="exposed"))
        self.assertEqual([k["overdue"] for k in s["kept"]], [False, True])

    def test_kev_turns_overdue_after_its_limit_from_the_date_cisa_added_it(self):
        limit = T.AGE_LIMIT_DAYS["kev"]
        feeds = {"kev": {"CVE-2099-0015": days_ago(limit)[:10], "CVE-2099-0016": days_ago(limit + 1)[:10]}, "epss": {}}
        rep = report(vuln("CVE-2099-0015"), vuln("CVE-2099-0016"))
        _, s, _ = run(rep, target("deploy/db"), classes(db="internal"), feeds)
        self.assertEqual([k["overdue"] for k in s["kept"]], [False, True])


class Exposure(unittest.TestCase):
    CFG = {
        "route_sources": {"proxy.conf": "", "writer.py": ""},
        "not_route_sources": {"audit.sh": ""},
        "runtime_upstreams": {"{tenant}": {"service": "api", "env": "CHAT_IMAGE", "reason": "tenant route"}},
        "content_consumers": {"crawler": "renders hostile pages"},
        "internal": {"db": "database"},
    }
    DEPLOY = {"services": {
        "web": {"image": "example/web:1"},
        "api": {"image": "example/api:1", "environment": {"CHAT_IMAGE": "example/chat@sha256:aa"}},
        "chat-one": {"image": "example/chat@sha256:aa"},
        "crawler": {"image": "example/crawler:1"},
        "db": {"image": "example/db:1", "ports": ["127.0.0.1:5432:5432"]},
        "edge": {"image": "example/edge:1", "ports": ["443:443"]},
        "newcomer": {"image": "example/new:1"},
    }}
    ROUTES = {
        "proxy.conf": "site {\n    reverse_proxy h2c://web:8080 {\n    }\n}\n",
        "writer.py": 'f"""\n    reverse_proxy {tenant}:3080\n"""',
    }

    def classify(self, routes=None):
        return T.classify(self.DEPLOY, {"services": {"db": {}}}, self.CFG, routes or self.ROUTES)

    def test_exposure_is_derived_from_routes_runtime_routes_and_public_ports(self):
        c = self.classify()
        self.assertEqual({k: v[0] for k, v in c.items()}, {
            "deploy/web": "exposed", "deploy/chat-one": "exposed", "deploy/edge": "exposed",
            "deploy/crawler": "content", "deploy/db": "internal", "deploy/api": "unknown",
            "deploy/newcomer": "unknown", "dev/db": "internal",
        })

    def test_an_unclassifiable_service_is_treated_as_exposed_and_named(self):
        _, s, _ = run(report(vuln("CVE-2099-0020", "CRITICAL")), target("deploy/newcomer"), self.classify())
        self.assertEqual(s["exposure"], "unknown")
        self.assertEqual(s["unknown_services"], ["deploy/newcomer"])
        self.assertEqual(s["kept"][0]["reasons"], ["critical-exposed"])
        self.assertIn("Unclassified, treated as exposed: deploy/newcomer", T.summary_markdown([s]))

    def test_a_route_to_an_unknown_host_stops_the_run(self):
        with self.assertRaisesRegex(T.TriageError, "neither a compose service"):
            self.classify({"proxy.conf": "    reverse_proxy ghost:80\n"})

    def test_a_runtime_route_to_an_image_no_service_runs_stops_the_run(self):
        deploy = json.loads(json.dumps(self.DEPLOY))
        deploy["services"]["api"]["environment"]["CHAT_IMAGE"] = "example/chat@sha256:bb"
        with self.assertRaisesRegex(T.TriageError, "no compose service runs"):
            T.classify(deploy, {}, self.CFG, self.ROUTES)

    def test_a_file_that_mentions_reverse_proxy_must_be_classified(self):
        mentions = ["proxy.conf", "audit.sh", "new/writer.ts", "svc/tests/test_routes.py", "docs/proxy.md"]
        self.assertEqual(T.unlisted_route_sources(mentions, self.CFG), ["new/writer.ts"])


class StaleSbom(unittest.TestCase):
    def lang_result(self):
        installed = {"Name": "requests", "Version": "9.0", "FilePath": "site-packages/requests-9.0.dist-info/METADATA",
                     "Identifier": {}, "Layer": {"DiffID": "L1"}}
        project = {"Name": "requests", "Version": "1.0", "Identifier": {"BOMRef": "pkg:pypi/requests@1.0"}, "Layer": {"DiffID": "LPROJ"}}
        project_extra = {"Name": "oldlib", "Version": "1.0", "Identifier": {"BOMRef": "pkg:pypi/oldlib@1.0"}, "Layer": {"DiffID": "LPROJ"}}
        # A vendor SBOM written with the tool that vendors the libraries: its
        # layer holds an installed package, and its vendored requests differs
        # from the installed one without being a stale inventory.
        tool = {"Name": "tool", "Version": "3.0", "FilePath": "site-packages/tool-3.0.dist-info/METADATA",
                "Identifier": {}, "Layer": {"DiffID": "LVEND"}}
        vendored = {"Name": "requests", "Version": "2.0", "Identifier": {"BOMRef": "pkg:pypi/requests@2.0"}, "Layer": {"DiffID": "LVEND"}}
        vulns = [
            vuln("CVE-2099-0030", "CRITICAL", "requests", layer="LPROJ", bomref=True),
            vuln("CVE-2099-0031", "CRITICAL", "oldlib", layer="LPROJ", bomref=True),
            vuln("CVE-2099-0032", "CRITICAL", "requests", layer="LVEND", bomref=True),
            vuln("CVE-2099-0033", "CRITICAL", "requests", path="site-packages/requests-9.0.dist-info/METADATA"),
        ]
        packages = [installed, project, project_extra, tool, vendored]
        return {"Target": "Python", "Class": "lang-pkgs", "Packages": packages, "Vulnerabilities": vulns}

    def test_a_project_sbom_contradicted_by_installed_packages_is_dropped_whole(self):
        _, s, _ = run(report(results=[self.lang_result()]), target("deploy/web"), classes(web="exposed"))
        self.assertEqual(s["dropped"]["stale-sbom"], 2)

    def test_a_vendor_sbom_installed_with_its_tool_is_kept(self):
        out, s, _ = run(report(results=[self.lang_result()]), target("deploy/web"), classes(web="exposed"))
        self.assertEqual(sorted(kept_ids(s)), ["CVE-2099-0032", "CVE-2099-0033"])
        kept = sorted(v["VulnerabilityID"] for v in out["Results"][0]["Vulnerabilities"])
        self.assertEqual(kept, ["CVE-2099-0032", "CVE-2099-0033"])


class Vex(unittest.TestCase):
    DEPLOY = {"services": {"cal": {"image": "example/cal:1", "environment": {"TZ": "UTC"}}}}

    def stmt(self, **over):
        s = {
            "id": "cal-tool", "services": ["deploy/cal"], "component": {"path": "usr/local/bin/tool"},
            "justification": "vulnerable_code_not_in_execute_path",
            "reason": "the entrypoint starts the bundled tool only when REPLICA is set",
            "evidence": ["entrypoint script"], "conditions": [{"service": "deploy/cal", "env_unset": ["REPLICA"]}],
            "expires": (NOW + dt.timedelta(days=30)).isoformat(),
        }
        s.update(over)
        return s

    def rep(self):
        tool = vuln("CVE-2099-0040", "CRITICAL", "stdlib", purl="pkg:golang/stdlib@v1.0")
        return report(results=[{"Target": "usr/local/bin/tool", "Class": "lang-pkgs", "Vulnerabilities": [tool]}])

    def test_a_valid_statement_drops_the_component_and_emits_openvex(self):
        _, s, doc = run(self.rep(), target("deploy/cal"), classes(cal="exposed"), vex={"statements": [self.stmt()]}, deploy=self.DEPLOY)
        self.assertEqual((s["kept"], s["dropped"]["vex"], s["vex_applied"]), ([], 1, ["cal-tool"]))
        self.assertEqual(doc["statements"][0]["vulnerability"]["name"], "CVE-2099-0040")
        self.assertEqual(doc["statements"][0]["status"], "not_affected")

    def test_an_expired_statement_is_not_applied_and_fails_validation(self):
        old = self.stmt(expires=(NOW - dt.timedelta(days=1)).isoformat())
        _, s, _ = run(self.rep(), target("deploy/cal"), classes(cal="exposed"), vex={"statements": [old]}, deploy=self.DEPLOY)
        self.assertEqual(kept_ids(s), ["CVE-2099-0040"])
        self.assertIn("expired", s["vex_skipped"][0]["why"][0])
        self.assertTrue(any("expired" in e for e in T.validate_vex({"statements": [old]}, self.DEPLOY, {}, NOW)))

    def test_a_statement_whose_compose_condition_no_longer_holds_is_not_applied(self):
        deploy = {"services": {"cal": {"image": "example/cal:1", "environment": {"REPLICA": "s3://bucket"}}}}
        _, s, _ = run(self.rep(), target("deploy/cal"), classes(cal="exposed"), vex={"statements": [self.stmt()]}, deploy=deploy)
        self.assertEqual(kept_ids(s), ["CVE-2099-0040"])
        self.assertEqual(T.validate_vex({"statements": [self.stmt()]}, deploy, {}, NOW), ["cal-tool: deploy/cal sets REPLICA"])

    def test_validation_requires_reason_justification_and_bounded_expiry(self):
        bad = self.stmt(reason="unused", justification="not_used", expires=(NOW + dt.timedelta(days=400)).isoformat())
        errors = T.validate_vex({"statements": [bad]}, self.DEPLOY, {}, NOW)
        self.assertEqual(len(errors), 3, errors)

    def test_the_seeded_file_is_valid(self):
        done = subprocess.run(["sh", str(ROOT / "scripts" / "validate-vex.sh")], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)


class Feeds(unittest.TestCase):
    KEV = {"catalogVersion": "2099.01.01", "count": 1, "vulnerabilities": [{"cveID": "CVE-2099-0050", "dateAdded": "2099-01-01"}]}
    EPSS = {"status": "OK", "data": [{"cve": "CVE-2099-0051", "epss": "0.5"}]}

    def opener(self, answers):
        calls = []

        def urlopen(req, timeout):
            calls.append(req.full_url)
            answer = answers(req.full_url)
            if isinstance(answer, Exception):
                raise answer
            return io.BytesIO(json.dumps(answer).encode())

        return urlopen, calls

    def test_both_feeds_are_joined(self):
        urlopen, _ = self.opener(lambda u: self.KEV if u.startswith(T.KEV_URL) else self.EPSS)
        feeds = T.fetch_feeds({"CVE-2099-0050", "CVE-2099-0051", "GHSA-xxxx"}, urlopen, lambda s: None)
        self.assertEqual((feeds["kev"], feeds["epss"]), ({"CVE-2099-0050": "2099-01-01"}, {"CVE-2099-0051": 0.5}))

    def test_an_unreachable_feed_fails_after_retries_instead_of_reporting_nothing(self):
        urlopen, calls = self.opener(lambda u: OSError("down") if u.startswith(T.EPSS_URL) else self.KEV)
        with self.assertRaisesRegex(T.TriageError, "unreachable after 3 attempts"):
            T.fetch_feeds({"CVE-2099-0051"}, urlopen, lambda s: None)
        self.assertEqual(sum(c.startswith(T.EPSS_URL) for c in calls), T.FEED_ATTEMPTS)

    def test_a_kev_answer_without_its_catalog_fails(self):
        urlopen, _ = self.opener(lambda u: {"count": 0, "vulnerabilities": []})
        with self.assertRaisesRegex(T.TriageError, "without its catalog"):
            T.fetch_feeds(set(), urlopen, lambda s: None)

    def test_epss_batches_stay_within_the_api_limits(self):
        batches = list(T.epss_batches({f"CVE-2099-{i:06d}" for i in range(250)}))
        self.assertTrue(all(len(b) <= T.EPSS_BATCH and len(",".join(b)) <= T.EPSS_MAX_PARAM for b in batches))
        self.assertEqual(sum(map(len, batches)), 250)


class Targets(unittest.TestCase):
    def test_targets_group_by_image_skip_our_builds_and_swap_the_crawler_base(self):
        deploy = {"services": {
            "a": {"image": "example/db:1"}, "b": {"image": "example/db:1"},
            "own": {"image": "ghcr.io/getklai/portal-api:latest"},
            "crawl": {"image": "ghcr.io/getklai/crawl4ai:1-local-990101-0000"},
            "local": {"image": "example/tool:2-local-990101-1200"},
        }}
        out = T.targets(deploy, {"services": {"a": {"image": "example/db:1"}}}, "example/crawler:1")
        self.assertEqual([(t["key"], t["image"]) for t in out],
                         [("deploy_a__deploy_b__dev_a", "example/db:1"), ("deploy_crawl", "example/crawler:1")])
        self.assertEqual(out[0]["category"], "pinned-service-deploy_a__deploy_b__dev_a")

    def test_stale_categories_are_this_jobs_categories_with_open_alerts_and_no_target(self):
        def alert(cat, key=T.SCAN_JOB_KEY, state="open", tool="Trivy"):
            return {"state": state, "tool": {"name": tool}, "most_recent_instance": {"analysis_key": key, "category": cat}}

        alerts = [alert("pinned-service-a"), alert("pinned-image-old"), alert("pinned-image-old"), alert("pinned-gone", state="fixed"),
                  alert("pinned-other", key=".github/workflows/x.yml:scan"), alert("pinned-codeql", tool="CodeQL")]
        self.assertEqual(T.stale_categories(alerts, {"pinned-service-a"}), ["pinned-image-old"])


class ReportAndGate(unittest.TestCase):
    def summary(self, image, exposure, kept, services=("deploy/web",), kev_running=()):
        return {"key": image, "image": image, "services": list(services), "exposure": exposure, "total": 10, "kept": kept,
                "dropped": {"stale-sbom": 1, "vex": 2, "rule": 10 - len(kept) - 3}, "unknown_services": [], "vex_skipped": [],
                "kev_running": list(kev_running)}

    def row(self, vid, reasons, pkg="libexample"):
        return {"id": vid, "package": pkg, "reasons": reasons}

    def test_the_summary_has_one_row_per_image_and_a_total(self):
        md = T.summary_markdown([self.summary("example/a:1", "exposed", [self.row("CVE-2099-0060", ["kev", "epss"])]),
                                 self.summary("example/b:1", "internal", [])])
        self.assertIn("| `example/a:1` | exposed | 10 | 1 | epss 1, kev 1 | 1 / 2 / 6 |", md)
        self.assertIn("| **all 2 images** | | 20 | 1 | | |", md)

    def test_kev_on_a_running_image_fails_the_weekly_summary(self):
        with tempfile.TemporaryDirectory() as d:
            T.write(Path(d) / "a.summary.json", self.summary("example/a:1", "internal", [], kev_running=["CVE-2099-0061"]))
            report_path = Path(d) / "report.json"
            self.assertEqual(T.main(["summary", "--summaries", d, "--report", str(report_path), "--fail-on-kev"]), 1)
            self.assertEqual(T.load(report_path), {"kev_running": [{"id": "CVE-2099-0061", "image": "example/a:1"}]})

    def test_the_gate_fails_only_on_a_kev_finding_the_change_adds_on_an_exposed_image(self):
        kev = self.row("CVE-2099-0070", ["kev"])
        base = [self.summary("example/web:1", "exposed", [])]
        self.assertEqual(len(T.introduced_kev(base, [self.summary("example/web:2", "exposed", [kev])])), 1)
        same_before = [self.summary("example/web:1", "exposed", [kev])]
        self.assertEqual(T.introduced_kev(same_before, [self.summary("example/web:2", "exposed", [kev])]), [])
        self.assertEqual(T.introduced_kev(base, [self.summary("example/web:2", "internal", [kev])]), [])

    def test_changed_targets_are_new_images_with_their_base_counterparts(self):
        base = [target("deploy/web", image="example/web:1"), target("deploy/db", image="example/db:1")]
        head = [target("deploy/web", image="example/web:2"), target("deploy/db", image="example/db:1")]
        out = T.changed_targets(base, head)
        self.assertEqual(([t["image"] for t in out["head"]], [t["image"] for t in out["base"]]), (["example/web:2"], ["example/web:1"]))


# Per image, the shape of the 2026-11-24 list: (exposure, findings, critical,
# from a stale project SBOM, covered by a VEX statement, KEV ids, EPSS >= 0.1).
REPLAY_SHAPE = [
    ("content", 308, 38, 302, 0, 2, 3), ("exposed", 234, 7, 0, 0, 0, 0), ("internal", 161, 12, 0, 0, 1, 1),
    ("exposed", 121, 4, 0, 34, 0, 0), ("internal", 71, 8, 0, 38, 0, 0), ("exposed", 60, 3, 0, 60, 0, 2),
    ("exposed", 57, 1, 0, 0, 0, 0), ("content", 41, 0, 0, 0, 0, 0), ("internal", 26, 1, 0, 26, 0, 0),
    ("exposed", 25, 2, 0, 0, 0, 0), ("internal", 20, 3, 0, 0, 0, 0), ("internal", 20, 3, 0, 0, 0, 0),
    ("exposed", 20, 0, 0, 0, 0, 0), ("content", 18, 0, 0, 0, 0, 0), ("internal", 11, 0, 0, 0, 0, 0),
    ("internal", 10, 0, 0, 0, 0, 0), ("internal", 9, 0, 0, 0, 0, 0), ("exposed", 6, 0, 0, 0, 0, 0),
    ("exposed", 5, 0, 0, 0, 0, 0), ("exposed", 3, 0, 0, 0, 0, 0), ("internal", 3, 0, 0, 0, 0, 0),
    ("internal", 3, 2, 0, 1, 1, 1), ("exposed", 2, 0, 0, 0, 0, 0), ("content", 1, 1, 0, 0, 0, 0),
]


def replay_report(rng, ids, kev, epss, shape):
    _, total, critical, sbom, vexed, n_kev, n_epss = shape
    installed = {"Name": "pkg-installed", "Version": "9.0", "FilePath": "site-packages/pkg_installed/METADATA",
                 "Identifier": {}, "Layer": {"DiffID": "L1"}}
    stale = {"Name": "pkg-installed", "Version": "1.0", "Identifier": {"BOMRef": "pkg:pypi/pkg-installed@1.0"},
             "Layer": {"DiffID": "LPROJ"}}
    os_vulns, lang_vulns, tool_vulns = [], [], []
    for i in range(total):
        vid = f"CVE-2099-{next(ids):05d}"
        severity = "CRITICAL" if i < critical else "HIGH"
        if i < n_kev:
            kev[vid] = days_ago(rng.randint(1, 60))[:10]
        elif i < n_kev + n_epss:
            epss[vid] = rng.uniform(T.EPSS_THRESHOLD, 0.9)
        else:
            epss[vid] = rng.uniform(0.0001, 0.05)
        published = rng.randint(5, 60)
        if i >= total - sbom:
            lang_vulns.append(vuln(vid, severity, "pkg-installed", layer="LPROJ", bomref=True, published=published))
        elif i >= total - sbom - vexed:
            tool_vulns.append(vuln(vid, severity, "stdlib", purl="pkg:golang/stdlib@v1.0", published=published))
        else:
            os_vulns.append(vuln(vid, severity, published=published))
    return report(results=[
        {"Target": "example (debian 99)", "Class": "os-pkgs", "Vulnerabilities": os_vulns},
        {"Target": "Python", "Class": "lang-pkgs", "Packages": [installed, stale], "Vulnerabilities": lang_vulns},
        {"Target": "usr/local/bin/tool", "Class": "lang-pkgs", "Vulnerabilities": tool_vulns},
    ])


class Replay(unittest.TestCase):
    def test_a_week_shaped_like_2026_11_24_reduces_to_a_short_list(self):
        rng = random.Random(20261124)
        ids, kev, epss, summaries = iter(range(1, 10_000)), {}, {}, []
        for n, shape in enumerate(REPLAY_SHAPE):
            svc = f"svc{n}"
            rep = replay_report(rng, ids, kev, epss, shape)
            vex = {"statements": [{"id": "tool", "services": [f"deploy/{svc}"], "component": {"path": "usr/local/bin/tool"},
                                   "justification": "vulnerable_code_not_in_execute_path", "reason": "x" * 40,
                                   "evidence": ["e"], "conditions": [], "expires": "2099-12-31"}]}
            _, s, _ = run(rep, target(f"deploy/{svc}"), classes(**{svc: shape[0]}), {"kev": kev, "epss": epss}, vex)
            summaries.append(s)
        kept = sum(len(s["kept"]) for s in summaries)
        self.assertEqual(sum(s["total"] for s in summaries), sum(shape[1] for shape in REPLAY_SHAPE))
        self.assertEqual(sum(s["dropped"]["stale-sbom"] for s in summaries), 302)
        self.assertEqual(sum(s["dropped"]["vex"] for s in summaries), 159)
        # The tab must stay a list a person reads in one sitting; the triage by
        # hand on 2026-11-24 gave 19 rows. A rule change that lets it grow past
        # 40 is a regression, not a finding.
        self.assertGreaterEqual(kept, 10)
        self.assertLessEqual(kept, 40, T.summary_markdown(summaries))


if __name__ == "__main__":
    unittest.main()
