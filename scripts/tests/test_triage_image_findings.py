"""Tests for scripts/triage-image-findings.py.

Every Trivy report, compose file and feed below is built in memory from the
helpers in this file (synthetic ids such as CVE-2099-0001, example/... image
names); no scan output is stored in the repository. The replay test draws a
report set from a seeded generator whose per-image counts follow the shape of
the 2026-11-24 alert list (counts only, no values from it).

Run: python3 -m unittest scripts/tests/test_triage_image_findings.py
"""

from __future__ import annotations

import contextlib
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


class UnfixedAndAliases(unittest.TestCase):
    def test_an_unfixed_kev_finding_is_kept_on_an_internal_image(self):
        feeds = {"kev": {"CVE-2099-0101": "2099-05-30"}, "epss": {}}
        _, s, _ = run(report(vuln("CVE-2099-0101", fixed="")), target("deploy/db"), classes(db="internal"), feeds)
        self.assertEqual(s["kept"][0]["reasons"], ["kev"])

    def test_an_unfixed_high_epss_finding_is_kept_only_when_the_image_is_not_internal(self):
        feeds = {"kev": {}, "epss": {"CVE-2099-0102": 0.5}}
        rep = report(vuln("CVE-2099-0102", fixed=""))
        for cls, kept in (("exposed", True), ("reachable", True), ("internal", False)):
            _, s, _ = run(rep, target("deploy/svc"), classes(svc=cls), feeds)
            self.assertEqual(bool(s["kept"]), kept, cls)

    def test_an_unfixed_critical_without_kev_or_epss_is_dropped_even_when_exposed(self):
        _, s, _ = run(report(vuln("CVE-2099-0103", "CRITICAL", fixed="")), target("deploy/web"), classes(web="exposed"))
        self.assertEqual(s["kept"], [])

    def test_an_advisory_id_is_joined_to_the_feeds_through_its_cve_alias(self):
        v = vuln("GHSA-aaaa-bbbb-cccc")
        v["References"] = ["https://nvd.example.com/vuln/detail/CVE-2099-0104"]
        _, s, _ = run(report(v), target("deploy/db"), classes(db="internal"), {"kev": {"CVE-2099-0104": "2099-05-30"}, "epss": {}})
        self.assertEqual(s["kept"][0]["reasons"], ["kev"])
        _, s, _ = run(report(vuln("GHSA-dddd-eeee-ffff")), target("deploy/db"), classes(db="internal"))
        self.assertEqual(s["no_cve"], 1)


class Exposure(unittest.TestCase):
    CFG = {
        "route_sources": {"proxy.conf": "", "writer.py": ""},
        "not_route_sources": {"audit.sh": ""},
        "runtime_upstreams": {"{tenant}": {"service": "api", "env": "CHAT_IMAGE", "reason": "tenant route"}},
        "content_consumers": {"crawler": "renders hostile pages", "runtime.BOT_IMAGE": "browser in external meetings"},
        "internal": {"db": "database", "vault": "secrets store"},
    }
    DEPLOY = {"services": {
        "web": {"image": "example/web:1", "networks": ["front"]},
        "api": {"image": "example/api:1", "networks": ["front"], "environment": {"CHAT_IMAGE": "example/chat@sha256:" + "a" * 64}},
        "chat-one": {"image": "example/chat@sha256:" + "a" * 64, "networks": ["front"]},
        "crawler": {"image": "example/crawler:1", "networks": ["crawl"]},
        "cache": {"image": "example/cache:1", "networks": ["crawl"]},
        "db": {"image": "example/db:1", "networks": ["back"], "ports": ["127.0.0.1:5432:5432"]},
        "vault": {"image": "example/vault:1", "networks": ["front"]},
        "edge": {"image": "example/edge:1", "networks": ["edge"], "ports": ["443:443"]},
        "runtime": {"image": "example/runtime:1", "networks": ["bots"], "environment": {"BOT_IMAGE": "example/bot:2"}},
        "newcomer": {"image": "example/new:1", "networks": ["island"]},
    }}
    ROUTES = {
        "proxy.conf": "site {\n    reverse_proxy h2c://web:8080 {\n    }\n}\n",
        "writer.py": 'f"""\n    reverse_proxy {tenant}:3080\n"""',
    }

    def classify(self, routes=None, deploy=None):
        return T.classify(deploy or self.DEPLOY, {"services": {"db": {}}}, self.CFG, routes or self.ROUTES)

    def test_exposure_is_derived_and_reachable_services_sit_between_exposed_and_internal(self):
        c = self.classify()
        self.assertEqual({k: v[0] for k, v in c.items()}, {
            "deploy/web": "exposed", "deploy/chat-one": "exposed", "deploy/edge": "exposed",
            "deploy/api.CHAT_IMAGE": "exposed", "deploy/crawler": "content", "deploy/runtime.BOT_IMAGE": "content",
            "deploy/api": "reachable", "deploy/cache": "reachable", "deploy/vault": "reachable",
            "deploy/db": "internal", "deploy/runtime": "unknown", "deploy/newcomer": "unknown", "dev/db": "internal",
        })
        self.assertEqual(c["deploy/vault"][1], "shares network front with chat-one")

    def test_a_critical_on_a_reachable_image_is_kept_like_on_an_exposed_one(self):
        _, s, _ = run(report(vuln("CVE-2099-0021", "CRITICAL")), target("deploy/cache"), self.classify())
        self.assertEqual((s["exposure"], s["kept"][0]["reasons"]), ("reachable", ["critical-exposed"]))

    def test_an_unclassifiable_service_is_treated_as_exposed_and_named(self):
        c = self.classify()
        _, s, _ = run(report(vuln("CVE-2099-0020", "CRITICAL")), target("deploy/newcomer"), c)
        self.assertEqual(s["exposure"], "unknown")
        self.assertEqual(s["kept"][0]["reasons"], ["critical-exposed"])
        notes = T.architecture_notes([target("deploy/newcomer")], c, {}, self.DEPLOY, {}, NOW, None)
        self.assertEqual(notes, {"- Unclassified, treated as exposed: deploy/newcomer"})

    def test_a_route_to_an_unknown_host_stops_the_run(self):
        with self.assertRaisesRegex(T.TriageError, "neither a compose service"):
            self.classify({"proxy.conf": "    reverse_proxy ghost:80\n"})

    def test_a_runtime_route_to_an_image_pinned_only_in_the_environment_is_exposed(self):
        deploy = json.loads(json.dumps(self.DEPLOY))
        deploy["services"]["api"]["environment"]["CHAT_IMAGE"] = "example/chat@sha256:" + "b" * 64
        c = self.classify(deploy=deploy)
        self.assertEqual((c["deploy/api.CHAT_IMAGE"][0], c["deploy/chat-one"][0]), ("exposed", "reachable"))

    def test_a_file_that_mentions_reverse_proxy_must_be_classified(self):
        mentions = ["proxy.conf", "audit.sh", "new/writer.ts", "svc/tests/test_routes.py", "docs/proxy.md"]
        self.assertEqual(T.unlisted_route_sources(mentions, self.CFG), ["new/writer.ts"])


class StaleSbom(unittest.TestCase):
    def lang_result(self):
        installed = {"Name": "requests", "Version": "9.0", "FilePath": "site-packages/requests-9.0.dist-info/METADATA",
                     "Identifier": {"PURL": "pkg:pypi/requests@9.0"}, "Layer": {"DiffID": "L1"}}
        project = {"Name": "requests", "Version": "1.0", "Identifier": {"PURL": "pkg:pypi/requests@1.0", "BOMRef": "x"},
                   "Layer": {"DiffID": "LPROJ"}}
        project_extra = {"Name": "oldlib", "Version": "1.0", "Identifier": {"PURL": "pkg:pypi/oldlib@1.0", "BOMRef": "y"},
                         "Layer": {"DiffID": "LPROJ"}}
        # A vendor SBOM written with the tool that vendors the libraries: its
        # layer holds an installed package, and its vendored requests differs
        # from the installed one without being a stale inventory.
        tool = {"Name": "tool", "Version": "3.0", "FilePath": "site-packages/tool-3.0.dist-info/METADATA",
                "Identifier": {"PURL": "pkg:pypi/tool@3.0"}, "Layer": {"DiffID": "LVEND"}}
        vendored = {"Name": "requests", "Version": "2.0", "Identifier": {"PURL": "pkg:pypi/requests@2.0", "BOMRef": "z"},
                    "Layer": {"DiffID": "LVEND"}}
        vulns = [
            vuln("CVE-2099-0030", "CRITICAL", "requests", layer="LPROJ", bomref=True, purl="pkg:pypi/requests@1.0"),
            vuln("CVE-2099-0031", "CRITICAL", "oldlib", layer="LPROJ", bomref=True, purl="pkg:pypi/oldlib@1.0"),
            vuln("CVE-2099-0032", "CRITICAL", "requests", layer="LVEND", bomref=True, purl="pkg:pypi/requests@2.0"),
            vuln("CVE-2099-0033", "CRITICAL", "requests", path="site-packages/requests-9.0.dist-info/METADATA",
                 purl="pkg:pypi/requests@9.0"),
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

    def test_a_known_exploited_finding_is_never_dropped_by_the_sbom_rule(self):
        feeds = {"kev": {"CVE-2099-0030": "2099-05-30"}, "epss": {"CVE-2099-0031": 0.5}}
        _, s, _ = run(report(results=[self.lang_result()]), target("deploy/web"), classes(web="exposed"), feeds)
        self.assertEqual(s["dropped"]["stale-sbom"], 0)
        self.assertIn("CVE-2099-0030", kept_ids(s))
        self.assertIn("CVE-2099-0031", kept_ids(s))

    def test_installed_is_computed_across_the_image_not_per_result(self):
        # The stale SBOM's pypi components landed in a second result while the
        # installed copy sits in the first; both results are judged together.
        r = self.lang_result()
        moved = {"Target": "Python (sbom)", "Class": "lang-pkgs",
                 "Packages": [p for p in r["Packages"] if p["Layer"]["DiffID"] == "LPROJ"],
                 "Vulnerabilities": [v for v in r["Vulnerabilities"] if v["Layer"]["DiffID"] == "LPROJ"]}
        r["Packages"] = [p for p in r["Packages"] if p["Layer"]["DiffID"] != "LPROJ"]
        r["Vulnerabilities"] = [v for v in r["Vulnerabilities"] if v["Layer"]["DiffID"] != "LPROJ"]
        _, s, _ = run(report(results=[r, moved]), target("deploy/web"), classes(web="exposed"))
        self.assertEqual(s["dropped"]["stale-sbom"], 2)


class KernelHeaders(unittest.TestCase):
    def test_a_known_exploited_finding_on_the_kernel_header_package_is_dropped_and_counted(self):
        rep = report(vuln("CVE-2099-0040", "CRITICAL", "linux-libc-dev"), vuln("CVE-2099-0040", "CRITICAL", "libexample"))
        feeds = {"kev": {"CVE-2099-0040": {}}, "epss": {}}
        _, s, _ = run(rep, target("deploy/app"), classes(app="exposed"), feeds)
        self.assertEqual(s["dropped"]["kernel-headers"], 1)
        self.assertEqual([k["package"] for k in s["kept"]], ["libexample"])


class Vex(unittest.TestCase):
    DEPLOY = {"services": {"cal": {"image": "example/cal:1", "environment": {"TZ": "UTC"}}}}

    def stmt(self, **over):
        s = {
            "id": "cal-tool", "services": ["deploy/cal"], "component": {"path": "usr/local/bin/tool"},
            "justification": "vulnerable_code_not_in_execute_path",
            "reason": "the entrypoint starts the bundled tool only when REPLICA is set",
            "evidence": ["entrypoint script"], "conditions": [{"service": "deploy/cal", "env_unset": ["REPLICA"]}],
            "verified_against": ["example/cal:1"],
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

    def test_a_statement_without_a_condition_or_a_verified_pin_is_rejected(self):
        errors = T.validate_vex({"statements": [self.stmt(conditions=[], verified_against=[])]}, self.DEPLOY, {}, NOW)
        self.assertEqual(len(errors), 2, errors)

    def test_a_moved_pin_without_image_checks_needs_reverification(self):
        current = {"deploy/cal": "example/cal:2"}
        errors = T.validate_vex({"statements": [self.stmt()]}, self.DEPLOY, {}, NOW, current)
        self.assertEqual(errors, ["cal-tool: deploy/cal now runs example/cal:2, which this statement was not verified against; re-verify"])
        _, s, _ = T.triage(self.rep(), target("deploy/cal", image="example/cal:2"), classes(cal="exposed"), NO_FEEDS,
                           {"statements": [self.stmt()]}, self.DEPLOY, {"services": {}}, NOW, current)
        self.assertEqual(kept_ids(s), ["CVE-2099-0040"])

    def test_a_moved_pin_is_re_verified_automatically_from_the_image(self):
        stmt = self.stmt(image_checks=[{"path": "/entrypoint.sh", "contains": "REPLICA"}])
        current = {"deploy/cal": "example/cal:2"}
        self.assertEqual(T.validate_vex({"statements": [stmt]}, self.DEPLOY, {}, NOW, current, lambda i, p, c: True), [])
        failing = T.validate_vex({"statements": [stmt]}, self.DEPLOY, {}, NOW, current, lambda i, p, c: False)
        self.assertEqual(failing, ["cal-tool: deploy/cal now runs example/cal:2, and its image checks fail"])
        self.assertEqual(len(T.validate_vex({"statements": [stmt]}, self.DEPLOY, {}, NOW, current)), 1)

    def test_the_seeded_file_is_valid(self):
        done = subprocess.run(["sh", str(ROOT / "scripts" / "validate-vex.sh")], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_every_seeded_statement_has_a_condition_and_a_verified_pin(self):
        for stmt in T.load(ROOT / ".vex/unused-components.json")["statements"]:
            self.assertTrue(stmt["conditions"] and stmt["verified_against"], stmt["id"])


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

    def test_kev_and_epss_are_read_from_their_feeds(self):
        urlopen, _ = self.opener(lambda u: self.KEV if u.startswith(T.KEV_URL) else self.EPSS)
        self.assertEqual(T.fetch_kev(urlopen, lambda s: None), {"CVE-2099-0050": "2099-01-01"})
        self.assertEqual(T.fetch_epss({"CVE-2099-0051", "GHSA-xxxx"}, urlopen, lambda s: None), {"CVE-2099-0051": 0.5})

    def test_an_unreachable_feed_fails_after_retries_instead_of_reporting_nothing(self):
        urlopen, calls = self.opener(lambda u: OSError("down"))
        with self.assertRaisesRegex(T.TriageError, "unreachable after 3 attempts"):
            T.fetch_epss({"CVE-2099-0051"}, urlopen, lambda s: None)
        self.assertEqual(len(calls), T.FEED_ATTEMPTS)

    def test_a_kev_answer_without_its_catalog_fails(self):
        urlopen, _ = self.opener(lambda u: {"count": 0, "vulnerabilities": []})
        with self.assertRaisesRegex(T.TriageError, "without its catalog"):
            T.fetch_kev(urlopen, lambda s: None)

    def test_epss_batches_stay_within_the_api_limits(self):
        batches = list(T.epss_batches({f"CVE-2099-{i:06d}" for i in range(250)}))
        self.assertTrue(all(len(b) <= T.EPSS_BATCH and len(",".join(b)) <= T.EPSS_MAX_PARAM for b in batches))
        self.assertEqual(sum(map(len, batches)), 250)



class Targets(unittest.TestCase):
    def test_targets_group_by_image_skip_our_builds_and_build_the_crawler(self):
        deploy = {"services": {
            "a": {"image": "example/db:1"}, "b": {"image": "example/db:1"},
            "own": {"image": "ghcr.io/getklai/portal-api:latest"},
            "crawl": {"image": "ghcr.io/getklai/crawl4ai:1-local-990101-0000"},
            "local": {"image": "example/tool:2-local-990101-1200"},
        }}
        out = T.targets({"deploy": deploy, "dev": {"services": {"a": {"image": "example/db:1"}}}}, repo=ROOT)
        self.assertEqual([(t["key"], t["image"], t["build"]) for t in out],
                         [("deploy_a__deploy_b__dev_a", "example/db:1", ""),
                          ("deploy_crawl", "klai-scan/crawl4ai:build", "deploy/crawl4ai")])
        self.assertEqual(out[0]["category"], "pinned-service-deploy_a__deploy_b__dev_a")
        self.assertEqual(out[1]["context"], T.context_hash(ROOT, "deploy/crawl4ai"))

    def test_a_change_to_the_crawler_build_context_is_a_changed_pin(self):
        built = {"key": "deploy_crawl", "services": ["deploy/crawl"], "image": "klai-scan/crawl4ai:build", "context": "aaa"}
        out = T.changed_targets([built], [{**built, "context": "bbb"}])
        self.assertEqual((len(out["head"]), len(out["base"])), (1, 1))
        self.assertEqual(T.changed_targets([built], [built])["head"], [])

    def test_images_pinned_in_environment_values_and_settings_are_targets(self):
        digest = "example/chat@sha256:" + "c" * 64
        deploy = {"services": {
            "runtime": {"image": "example/runtime:1", "environment": {"BOT_IMAGE": "example/bot:2", "AGENT_IMAGE": ""}},
            "portal-api": {"image": "example/api:1",
                           "environment": {"CHAT_IMAGE": "example/chat:1", "CHAT_IMAGE_OVERRIDES": f"acme={digest}"}},
            "chat": {"image": "example/chat:1"},
        }}
        settings = 'class S:\n    chat_image: str = "example/chat:1"\n    side_image: str = "example/side:3"\n    kb_images_base_url: str = "https://x.example.com"\n'
        out = {t["image"]: t for t in T.targets({"deploy": deploy}, settings)}
        self.assertEqual(out["example/bot:2"]["services"], ["deploy/runtime.BOT_IMAGE"])
        self.assertEqual(out["example/chat:1"]["key"], "deploy_chat")  # the env reference does not rename the category
        self.assertEqual(out["example/chat:1"]["services"], ["deploy/chat", "deploy/portal-api.CHAT_IMAGE"])
        self.assertEqual(out[digest]["services"], ["deploy/portal-api.CHAT_IMAGE_OVERRIDES"])
        self.assertEqual(out["example/side:3"]["services"], ["deploy/portal-api.SIDE_IMAGE"])
        self.assertNotIn("https://x.example.com", out)

    def test_an_environment_image_that_is_not_a_plain_reference_stops_the_run(self):
        deploy = {"services": {"runtime": {"image": "example/runtime:1", "environment": {"BOT_IMAGE": "${BOT}"}}}}
        with self.assertRaisesRegex(T.TriageError, "not a plain image reference"):
            T.targets({"deploy": deploy})

    def test_a_renovate_image_pin_the_derivation_does_not_know_fails_closed(self):
        renovate = {"customManagers": [{"customType": "regex", "managerFilePatterns": ["/^stack\\.yml$/"],
                                        "matchStrings": ["BOT_IMAGE:\\s+(?<depName>example/bot):(?<currentValue>\\S+)"]}]}
        text = "services:\n  runtime:\n    environment:\n      BOT_IMAGE: example/bot:2\n"
        self.assertEqual(T.unrecognised_renovate_pins(renovate, {"stack.yml": text}, {"example/bot:2"}), [])
        self.assertEqual(T.unrecognised_renovate_pins(renovate, {"stack.yml": text}, set()),
                         ["stack.yml:4 is an image pin to Renovate that the scan targets do not include"])

    def test_the_repository_renovate_config_parses_and_its_bot_pin_is_a_target(self):
        renovate = T.load_json5((ROOT / "renovate.json5").read_text())
        patterns = [m for c in renovate["customManagers"] for m in c.get("matchStrings", [])]
        self.assertTrue(any("BROWSER_IMAGE" in p for p in patterns))

    def test_stale_categories_are_this_jobs_categories_with_open_alerts_and_no_target(self):
        def alert(cat, key=T.SCAN_JOB_KEY, state="open", tool="Trivy"):
            return {"state": state, "tool": {"name": tool}, "most_recent_instance": {"analysis_key": key, "category": cat}}

        alerts = [alert("pinned-service-a"), alert("pinned-image-old"), alert("pinned-image-old"), alert("pinned-gone", state="fixed"),
                  alert("pinned-other", key=".github/workflows/x.yml:scan"), alert("pinned-codeql", tool="CodeQL")]
        self.assertEqual(T.stale_categories(alerts, {"pinned-service-a"}), ["pinned-image-old"])


def alert(rule_id, category, state="open"):
    return {"state": state, "tool": {"name": "Trivy"}, "rule": {"id": rule_id},
            "most_recent_instance": {"analysis_key": T.SCAN_JOB_KEY, "category": category}}


class ReportAndGate(unittest.TestCase):
    def summary(self, image, exposure, kept, services=("deploy/web",)):
        key = image.replace("/", "_").replace(":", "_")
        return {"key": key, "category": f"pinned-service-{key}", "image": image, "services": list(services),
                "exposure": exposure, "kept": kept}

    def row(self, vid, reasons, pkg="libexample"):
        return {"id": vid, "package": pkg, "reasons": reasons}

    def test_the_report_counts_the_tab_over_the_whole_run(self):
        targets = [target("deploy/web", image="example/web:1"), target("dev/db", image="example/db:1"),
                   target("deploy/c", image="example/c:1")]
        alerts = [alert("CVE-2099-0060", targets[0]["category"]), alert("CVE-2099-0061", targets[0]["category"]),
                  alert("CVE-2099-0062", targets[1]["category"]), alert("GHSA-aaaa-bbbb-cccc", targets[1]["category"]),
                  alert("CVE-2099-0063", targets[0]["category"], state="fixed"), alert("CVE-2099-0064", "pinned-elsewhere")]
        counts = T.tab_counts(alerts, targets, {"CVE-2099-0060": "x", "CVE-2099-0062": "x"}, {"CVE-2099-0061": 0.5})
        self.assertEqual(counts, {"images": 3, "images_with_findings": 2, "findings": 4, "kev": 2, "epss": 1,
                                  "other": 1, "kev_running": 1, "no_cve": 1})

    def test_the_gate_fails_only_on_a_kev_finding_the_change_adds_on_an_image_that_is_not_internal(self):
        kev = self.row("CVE-2099-0070", ["kev"])
        base = [self.summary("example/web:1", "exposed", [])]
        self.assertEqual(len(T.introduced_kev(base, [self.summary("example/web:2", "exposed", [kev])])), 1)
        same_before = [self.summary("example/web:1", "exposed", [kev])]
        self.assertEqual(T.introduced_kev(same_before, [self.summary("example/web:2", "exposed", [kev])]), [])
        self.assertEqual(T.introduced_kev(base, [self.summary("example/web:2", "internal", [kev])]), [])

    def test_a_base_tag_that_vanished_upstream_does_not_block_the_bump(self):
        head = self.summary("example/web:2", "exposed", [self.row("CVE-2099-0071", ["kev"])])
        self.assertEqual(len(T.introduced_kev([], [head])), 1)
        self.assertEqual(T.introduced_kev([], [head], {head["category"]: {"CVE-2099-0071"}}), [])

    def test_changed_targets_are_new_images_with_their_base_counterparts(self):
        base = [target("deploy/web", image="example/web:1"), target("deploy/db", image="example/db:1")]
        head = [target("deploy/web", image="example/web:2"), target("deploy/db", image="example/db:1")]
        out = T.changed_targets(base, head)
        self.assertEqual(([t["image"] for t in out["head"]], [t["image"] for t in out["base"]]), (["example/web:2"], ["example/web:1"]))


class NothingPublicNamesAFinding(unittest.TestCase):
    """No public log, summary or artifact lets a reader tell a kept finding or which image has one."""

    VID, PKG, VERSION, IMAGE = "CVE-2099-0901", "libleakcheck", "7.7.7-leak", "example/leaky:9.9"

    def summary(self):
        v = vuln(self.VID, "CRITICAL", self.PKG)
        v["InstalledVersion"] = self.VERSION
        feeds = {"kev": {self.VID: "2099-05-01"}, "epss": {self.VID: 0.9}}
        _, s, _ = run(report(v), target("deploy/leaky", image=self.IMAGE), classes(leaky="exposed"), feeds)
        self.assertEqual(kept_ids(s), [self.VID])  # the fixture really has a kept KEV finding
        return s

    def assert_clean(self, text):
        for secret in (self.VID, self.PKG, self.VERSION, self.IMAGE, "deploy_leaky", "deploy/leaky", "leaky"):
            self.assertNotIn(secret, text)

    def test_the_report_names_no_finding_and_no_image(self):
        targets = [target("deploy/leaky", image=self.IMAGE), target("deploy/quiet", image="example/quiet:1")]
        counts = T.tab_counts([alert(self.VID, targets[0]["category"])], targets, {self.VID: "x"}, {})
        text = T.report_markdown(counts)
        self.assert_clean(text)
        self.assertIn("2 images; 1 have at least one finding", text)

    def test_the_report_cli_fails_with_the_generic_message(self):
        with tempfile.TemporaryDirectory() as d:
            tg = target("deploy/leaky", image=self.IMAGE)
            T.write(Path(d) / "alerts.json", [[alert(self.VID, tg["category"])]])
            T.write(Path(d) / "targets.json", [tg])
            T.write(Path(d) / "kev.json", {self.VID: "2099-05-01"})
            T.write(Path(d) / "deploy.json", {"services": {"leaky": {"image": self.IMAGE}}})
            T.write(Path(d) / "dev.json", {"services": {}})
            T.write(Path(d) / "exposure.json", {"route_sources": {}, "not_route_sources": {}, "runtime_upstreams": {},
                                                "content_consumers": {"leaky": "test reason here"}, "internal": {}})
            saved = T.fetch_epss, T.find_route_mentions
            T.fetch_epss, T.find_route_mentions = (lambda ids: {}), (lambda repo: [])
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    code = T.main(["report", "--alerts", f"{d}/alerts.json", "--targets", f"{d}/targets.json", "--kev", f"{d}/kev.json",
                                   "--deploy", f"{d}/deploy.json", "--dev", f"{d}/dev.json", "--exposure", f"{d}/exposure.json",
                                   "--vex", f"{d}/none.json", "--markdown", f"{d}/s.md"])
            finally:
                T.fetch_epss, T.find_route_mentions = saved
            self.assertEqual(code, 1)
            self.assertIn(T.NEEDS_ACTION, out.getvalue())
            self.assert_clean(out.getvalue() + Path(f"{d}/s.md").read_text())

    def test_the_gate_output_names_no_finding_nor_which_pin(self):
        with tempfile.TemporaryDirectory() as base, tempfile.TemporaryDirectory() as head:
            T.write(Path(head) / "x.summary.json", self.summary())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(T.main(["gate", "--base", base, "--head", head]), 1)
            self.assertIn(T.GATE_NEEDS_ACTION, out.getvalue())
            self.assert_clean(out.getvalue())

    def test_the_triage_cli_logs_nothing_and_writes_no_aggregate(self):
        with tempfile.TemporaryDirectory() as d:
            deploy = {"services": {"leaky": {"image": self.IMAGE}}}
            T.write(Path(d) / "deploy.json", deploy)
            T.write(Path(d) / "dev.json", {"services": {}})
            T.write(Path(d) / "targets.json", T.targets({"deploy": deploy}))
            T.write(Path(d) / "kev.json", {self.VID: "2099-05-01"})
            v = vuln(self.VID, "CRITICAL", self.PKG)
            v["InstalledVersion"] = self.VERSION
            T.write(Path(d) / "scan.json", report(v))
            T.write(Path(d) / "exposure.json", {"route_sources": {}, "not_route_sources": {}, "runtime_upstreams": {},
                                                "content_consumers": {}, "internal": {}})
            saved = T.fetch_epss, T.find_route_mentions
            T.fetch_epss, T.find_route_mentions = (lambda ids: {}), (lambda repo: [])
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    code = T.main(["triage", "--scan", f"{d}/scan.json", "--targets", f"{d}/targets.json", "--key", "deploy_leaky",
                                   "--kev", f"{d}/kev.json", "--deploy", f"{d}/deploy.json", "--dev", f"{d}/dev.json",
                                   "--out-dir", f"{d}/out", "--exposure", f"{d}/exposure.json", "--vex", f"{d}/none.json"])
            finally:
                T.fetch_epss, T.find_route_mentions = saved
            self.assertEqual(code, 0)
            self.assertEqual(T.load(f"{d}/out/deploy_leaky.summary.json")["kev_running"], [self.VID])
            self.assertEqual(sorted(p.name.split(".", 1)[1] for p in Path(f"{d}/out").iterdir()),
                             ["openvex.json", "summary.json", "triaged.json"])
            for secret in (self.VID, self.PKG, self.VERSION, self.IMAGE):
                self.assertNotIn(secret, out.getvalue())

    def test_no_workflow_uploads_a_per_image_artifact(self):
        def uploaded_paths(name):
            workflow = (ROOT / ".github/workflows" / name).read_text()
            paths = set()
            for block in workflow.split("actions/upload-artifact@")[1:]:
                body = block.split("\n      - ", 1)[0]
                after = body.split("path:", 1)[1].split("retention-days:", 1)[0]
                paths |= {p.strip() for p in after.replace("|", "").splitlines() if p.strip()}
            return paths

        self.assertEqual(uploaded_paths("scan-pinned-images.yml"),
                         {"inputs/deploy.json", "inputs/dev.json", "inputs/targets.json", "inputs/stale.json",
                          "kev.json"})
        self.assertEqual(uploaded_paths("image-pin-triage.yml"), set())

    def test_the_validator_rejects_an_identifier_in_the_tracked_files(self):
        doc = {"statements": [{"id": "x", "reason": "image has CVE-2099-0902 but it does not matter here"}]}
        found = T.identifier_mentions(doc)
        self.assertEqual(found, ["$.statements[0].reason names a vulnerability identifier; describe the component, not the finding"])
        self.assertNotIn("CVE-2099-0902", found[0])
        self.assertEqual(T.identifier_mentions(T.load(ROOT / ".vex/unused-components.json")), [])
        self.assertEqual(T.identifier_mentions(T.load(ROOT / "deploy/image-exposure.json")), [])


# Per image, the shape of the 2026-11-24 list: (exposure, findings, critical,
# from a stale project SBOM, covered by a VEX statement, KEV ids, EPSS >= 0.1).
# Every image that was internal that day is now "reachable": each shares a
# compose network with an exposed or content-consuming service.
REPLAY_SHAPE = [
    ("content", 308, 38, 302, 0, 2, 3), ("exposed", 234, 7, 0, 0, 0, 0), ("reachable", 161, 12, 0, 0, 1, 1),
    ("exposed", 121, 4, 0, 34, 0, 0), ("reachable", 71, 8, 0, 38, 0, 0), ("exposed", 60, 3, 0, 60, 0, 2),
    ("exposed", 57, 1, 0, 0, 0, 0), ("content", 41, 0, 0, 0, 0, 0), ("reachable", 26, 1, 0, 26, 0, 0),
    ("exposed", 25, 2, 0, 0, 0, 0), ("reachable", 20, 3, 0, 0, 0, 0), ("reachable", 20, 3, 0, 0, 0, 0),
    ("exposed", 20, 0, 0, 0, 0, 0), ("content", 18, 0, 0, 0, 0, 0), ("reachable", 11, 0, 0, 0, 0, 0),
    ("reachable", 10, 0, 0, 0, 0, 0), ("reachable", 9, 0, 0, 0, 0, 0), ("exposed", 6, 0, 0, 0, 0, 0),
    ("exposed", 5, 0, 0, 0, 0, 0), ("exposed", 3, 0, 0, 0, 0, 0), ("reachable", 3, 0, 0, 0, 0, 0),
    ("reachable", 3, 2, 0, 1, 1, 1), ("exposed", 2, 0, 0, 0, 0, 0), ("content", 1, 1, 0, 0, 0, 0),
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
        # hand on 2026-11-24 gave 19 rows. With every formerly internal image
        # now reachable this shape gives 49 (25 before that class existed);
        # a rule change that lets it grow past 60 is a regression.
        self.assertGreaterEqual(kept, 10)
        self.assertLessEqual(kept, 60)


if __name__ == "__main__":
    unittest.main()
