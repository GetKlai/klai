"""Consumer-side contract test for Klai's Vexa integration.

WHY THIS EXISTS
---------------
Klai depends on exactly seven Vexa HTTP calls and one webhook envelope. That
dependency currently lives only inside ``app/services/vexa.py`` and
``app/api/meetings.py`` — so "can we move to Vexa <new version>?" can only be
answered by re-reading upstream source by hand.

This module turns that question into a command. It pins upstream's own sealed
``api.v1`` contract and golden payloads as fixtures (see ``fixtures/vexa/MANIFEST.json``)
and asserts that everything Klai calls and parses is still there. Bumping the Vexa
image becomes: refresh the fixtures from the new tag, run this file, read the failures.

It runs fully offline. No Vexa service required. The one test that DOES need a live
service is marked ``vexa_live`` and is excluded from the default run.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does not prove Vexa behaves correctly — only that the shapes Klai depends on
still exist. Behaviour (does the bot actually join, does the webhook actually
deliver) is proven by the live smoke test and by a real meeting, not here.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

import pytest

from app.api.meetings import VexaWebhookPayload

FIXTURES = Path(__file__).parent / "fixtures" / "vexa"
GOLDEN = FIXTURES / "golden"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _golden(name: str) -> dict:
    return json.loads((GOLDEN / name).read_text())


# ---------------------------------------------------------------------------
# The declared surface: every Vexa call Klai makes, and why.
#
# This list IS the contract. Adding a call to VexaClient without adding it here
# fails test_declared_surface_matches_client_source — deliberately, because an
# undeclared call is an upgrade risk nobody wrote down.
# ---------------------------------------------------------------------------
KLAI_VEXA_SURFACE: list[tuple[str, str, str]] = [
    ("POST", "/bots", "VexaClient.start_bot"),
    ("DELETE", "/bots/{platform}/{native_meeting_id}", "VexaClient.stop_bot"),
    ("GET", "/bots/status", "VexaClient.get_running_bots + bot_poller"),
    ("GET", "/recordings", "VexaClient.get_recording (lookup)"),
    ("GET", "/recordings/{recording_id}/media/{media_file_id}/raw", "VexaClient.get_recording (download)"),
    ("DELETE", "/recordings/{recording_id}", "VexaClient.delete_recording"),
    ("GET", "/transcripts/{platform}/{native_meeting_id}", "VexaClient.get_transcript_segments"),
]

# Routes Klai calls that upstream declares in api.v1 but does NOT serve.
# Sourced from upstream's own KNOWN_GAPS.json waiver ledger. Each entry here is a
# known-degraded call path with a recorded consequence — not a silent acceptance.
KLAI_ROUTES_ON_UPSTREAM_WAIVER: dict[tuple[str, str], str] = {
    ("DELETE", "/recordings/{recording_id}"): (
        "Not served in the 0.12 carve (upstream issue #591). Klai's delete_recording() maps "
        "404 -> True, so on 0.12 a recording would be marked cleaned up without being deleted. "
        "Blast radius is currently nil because start_bot sends recording_enabled=False, so no "
        "recording is ever produced. If recordings are ever enabled, this MUST be revisited."
    ),
}


def _sealed_paths() -> dict[str, set[str]]:
    """{path: {METHOD, ...}} from upstream's sealed api.v1 OpenAPI document."""
    schema = _load("api.v1.schema.json")
    verbs = {"get", "post", "put", "delete", "patch"}
    return {path: {m.upper() for m in spec if m in verbs} for path, spec in schema.get("paths", {}).items()}


def _waived_routes() -> set[tuple[str, str]]:
    """{(METHOD, path), ...} upstream declares but does not serve."""
    gaps = _load("api.v1.KNOWN_GAPS.json")
    return {(g["method"].upper(), g["path"]) for g in gaps.get("known_gaps", [])}


# ---------------------------------------------------------------------------
# 1. Call-surface drift
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "caller"), KLAI_VEXA_SURFACE, ids=lambda v: str(v))
def test_klai_call_exists_in_sealed_contract(method: str, path: str, caller: str) -> None:
    """Every route Klai calls is declared in upstream's sealed api.v1.

    A failure here means the target Vexa version removed or renamed a route Klai
    depends on. Fix by adapting VexaClient, not by deleting the assertion.
    """
    sealed = _sealed_paths()
    assert path in sealed, f"{caller}: path {path!r} is absent from the sealed api.v1 contract"
    assert method in sealed[path], (
        f"{caller}: {method} {path} is absent from the sealed api.v1 contract "
        f"(declared methods: {sorted(sealed[path])})"
    )


def test_waived_routes_klai_depends_on_are_exactly_the_known_set() -> None:
    """Upstream's waiver ledger must not grow into Klai's call surface unnoticed.

    Upstream can waive a sealed route at any release (it is a diff-visible row in
    KNOWN_GAPS.json). If a NEW waiver lands on a route Klai calls, the call silently
    starts 404-ing. This test names the currently-accepted waivers; a new one goes red.
    """
    klai_paths = {(m, p) for m, p, _ in KLAI_VEXA_SURFACE}
    actually_waived = klai_paths & _waived_routes()
    expected_waived = set(KLAI_ROUTES_ON_UPSTREAM_WAIVER)

    newly_waived = actually_waived - expected_waived
    assert not newly_waived, (
        "Upstream now waives routes Klai calls that were previously served: "
        f"{sorted(newly_waived)}. Each needs a recorded consequence in "
        "KLAI_ROUTES_ON_UPSTREAM_WAIVER before this test may pass again."
    )

    no_longer_waived = expected_waived - actually_waived
    assert not no_longer_waived, (
        f"Upstream now serves {sorted(no_longer_waived)} again — remove the stale entry "
        "from KLAI_ROUTES_ON_UPSTREAM_WAIVER and re-enable the affected code path."
    )


def test_declared_surface_matches_client_source() -> None:
    """KLAI_VEXA_SURFACE covers every HTTP call VexaClient actually makes.

    Guards the drift direction the fixtures cannot see: a new call added to
    VexaClient that nobody declared, and that therefore never gets version-checked.
    """
    source = Path(__file__).resolve().parents[2] / "app" / "services" / "vexa.py"
    tree = ast.parse(source.read_text())

    found: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        verb = node.func.attr.upper()
        if verb not in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
            continue
        # self._http.<verb>(...) — the only HTTP surface in this module.
        target = node.func.value
        if not (isinstance(target, ast.Attribute) and target.attr == "_http"):
            continue
        if not node.args:
            continue
        found.add((verb, _path_shape(_literal_path(node.args[0]))))

    declared = {(m, _path_shape(p)) for m, p, _ in KLAI_VEXA_SURFACE}
    undeclared = found - declared
    assert not undeclared, (
        f"VexaClient calls routes that are not in KLAI_VEXA_SURFACE: {sorted(undeclared)}. "
        "Declare them so the next version bump checks them too."
    )

    unused = declared - found
    assert not unused, (
        f"KLAI_VEXA_SURFACE declares routes VexaClient no longer calls: {sorted(unused)}. "
        "Remove them so the surface stays an honest description of the dependency."
    )


_PLACEHOLDER = re.compile(r"\{[^}]*\}")


def _path_shape(path: str) -> str:
    """Compare paths by structure, not by placeholder name.

    VexaClient names its f-string locals `rec_id`/`mf_id`; upstream's contract names the
    same positions `recording_id`/`media_file_id`. Those are the same route. Normalising
    to `{}` keeps this test about "does Klai call an undeclared route" instead of about
    local variable naming.
    """
    return _PLACEHOLDER.sub("{}", path)


def _literal_path(node: ast.expr) -> str:
    """Render a URL argument as its templated form ('/bots/{platform}/{native_meeting_id}')."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):  # f-string
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                out.append("{" + _placeholder_name(part.value) + "}")
        return "".join(out)
    return "<dynamic>"


def _placeholder_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return str(node.slice.value)
    return "?"


# ---------------------------------------------------------------------------
# 2. Response-shape drift — upstream's own goldens through Klai's parsers
# ---------------------------------------------------------------------------


def test_running_bots_golden_carries_the_fields_klai_reads() -> None:
    """bot_poller correlates on (platform, native_meeting_id); status drives recovery."""
    payload = _golden("BotStatusResponse.example.json")
    bots = payload.get("running_bots")
    assert isinstance(bots, list) and bots, "BotStatusResponse must expose a non-empty running_bots list"
    for field in ("platform", "native_meeting_id", "status", "normalized_status"):
        assert field in bots[0], f"get_running_bots() reads {field!r}; absent from the upstream golden"


def test_transcript_golden_carries_the_segment_fields_klai_reads() -> None:
    """get_transcript_segments() -> meetings.run_transcription builds transcript_text from these."""
    payload = _golden("TranscriptionResponse.example.json")
    segments = payload.get("segments")
    assert isinstance(segments, list) and segments, "TranscriptionResponse must expose a non-empty segments list"
    for field in ("start", "end", "text", "speaker", "language"):
        assert field in segments[0], f"run_transcription reads segment[{field!r}]; absent from the upstream golden"


# ---------------------------------------------------------------------------
# 3. Webhook envelope drift
# ---------------------------------------------------------------------------


def test_meeting_completed_envelope_parses_to_klai_fields() -> None:
    """The 0.12 typed envelope must still land in VexaWebhookPayload shape 1."""
    envelope = _golden("Envelope.meeting-completed.json")
    parsed = VexaWebhookPayload.model_validate(envelope)

    meeting = envelope["data"]["meeting"]
    assert parsed.vexa_meeting_id == meeting["id"]
    assert parsed.platform == meeting["platform"]
    assert parsed.native_meeting_id == meeting["native_meeting_id"]
    assert parsed.status == "completed"
    assert parsed.ended_at == meeting["end_time"]


def test_meeting_completed_status_routes_to_the_completion_branch() -> None:
    """`completed` must NOT be in VEXA_STATUS_MAP — the handler branches on it explicitly.

    meetings.vexa_webhook() runs the completion path via `payload.status != "completed"`.
    If someone ever adds "completed" to the status map, the meeting would be marked with an
    intermediate status and the transcript fetch would never run.
    """
    from app.api import meetings as meetings_module

    source = Path(meetings_module.__file__).read_text()
    assert 'payload.status != "completed"' in source, (
        "The completion branch guard changed — re-verify that a status=completed webhook still "
        "reaches run_transcription()."
    )


def test_bot_failed_envelope_parses_to_failed_status() -> None:
    """bot.failed is the second (and only other) event the system webhook sink delivers."""
    envelope = _golden("Envelope.bot-failed.json")
    parsed = VexaWebhookPayload.model_validate(envelope)

    assert parsed.status == "failed"
    assert parsed.vexa_meeting_id == envelope["data"]["meeting"]["id"]
    assert parsed.platform == envelope["data"]["meeting"]["platform"]


def test_completed_envelope_carries_no_recording_id() -> None:
    """Documents a real consequence rather than leaving it to be rediscovered.

    Klai's cleanup_recording() is fed `payload.recording_id`, which shape 1 reads from
    `data.recording`. The 0.12 envelope has no such key, so recording cleanup is a no-op
    on that path. Consistent with recording_enabled=False and with the upstream waiver on
    DELETE /recordings — but it must be a deliberate, tested fact.
    """
    parsed = VexaWebhookPayload.model_validate(_golden("Envelope.meeting-completed.json"))
    assert parsed.recording_id is None
    assert "recording" not in _golden("Envelope.meeting-completed.json")["data"]


# ---------------------------------------------------------------------------
# 4. Signed delivery — the system-webhook auth Klai will receive
# ---------------------------------------------------------------------------


def _sign(payload_bytes: bytes, secret: str, timestamp: str) -> str:
    """Upstream's wire signature: sha256=hmac_sha256(secret, "<ts>." + payload)."""
    signed = f"{timestamp}.".encode() + payload_bytes
    return f"sha256={hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()}"


def test_signature_scheme_reproduces_the_upstream_golden() -> None:
    """Pins the exact HMAC construction so a receiver-side verifier can be trusted.

    The golden headers were produced by upstream's own sign_payload(). If this
    reproduction ever diverges, any signature check Klai adds would silently reject
    every real delivery.
    """
    headers = _golden("SignatureHeaders.signed.json")
    secret = headers["Authorization"].removeprefix("Bearer ")
    timestamp = headers["X-Webhook-Timestamp"]
    envelope = _golden("Envelope.meeting-completed.json")

    # The golden headers do not say which byte serialisation they were signed over,
    # and none of the plausible candidates reproduces the published digest:
    #   json.dumps default / compact separators / indent=2 / the golden file's own bytes.
    # Until upstream states it (or we capture a real delivery), any receiver-side
    # verifier we write would be guessing. This xfail keeps the open question in the
    # suite instead of in someone's head; delete it the moment a candidate matches.
    candidates = {
        "json.dumps(default)": json.dumps(envelope).encode(),
        "compact separators": json.dumps(envelope, separators=(",", ":")).encode(),
        "indent=2": json.dumps(envelope, indent=2).encode(),
        "golden file bytes": (GOLDEN / "Envelope.meeting-completed.json").read_bytes(),
    }
    matches = [name for name, b in candidates.items() if _sign(b, secret, timestamp) == headers["X-Webhook-Signature"]]
    if not matches:
        pytest.xfail(
            "Cannot reproduce the upstream golden signature from any known serialisation "
            f"({', '.join(candidates)}). Do NOT add signature verification to the webhook "
            "handler until this is resolved — it would reject every real delivery. "
            "Bearer-token auth (tested below) is the working check today."
        )
    assert matches, f"reproduced by: {matches}"  # pragma: no cover — xfail above when empty


def test_system_webhook_auth_header_matches_what_klai_already_checks() -> None:
    """0.12's system sink sends `Authorization: Bearer <secret>` alongside the HMAC.

    That is exactly what meetings._require_webhook_secret() validates today, which is
    why the 0.10 -> 0.12 webhook migration is a config change and not a handler rewrite.
    """
    headers = _golden("SignatureHeaders.signed.json")
    assert headers["Authorization"].startswith("Bearer ")

    from app.api import meetings as meetings_module

    source = Path(meetings_module.__file__).read_text()
    assert "Bearer" in source, "_require_webhook_secret no longer accepts a Bearer token"


def test_client_sends_the_user_id_header_0_12_authenticates_on() -> None:
    """0.12's POST /bots 401s without X-User-Id; Klai deploys no gateway to inject it.

    bot_spawn/router.py::_resolve_user_id raises 401 "Invalid user identity" when the
    header is absent or unparseable. Klai talks to meeting-api directly, so VexaClient
    must set it. Regression guard: an adversarial pass found this missing after the
    parallel stack was already written and committed.
    """
    from app.services.vexa import VexaClient

    client = VexaClient()
    try:
        assert client._http.headers.get("X-User-Id"), "VexaClient must send X-User-Id (0.12 spawn auth)"
        assert int(client._http.headers["X-User-Id"]) >= 0, "X-User-Id must parse as an int upstream"
    finally:
        pass


# ---------------------------------------------------------------------------
# 5. Deployment invariants
#
# The API-contract tests above cannot see these: they are properties of how the
# stack is wired, not of what upstream serves. Every one of them corresponds to a
# blocking defect an adversarial pass found in the first cut of the parallel stack.
# ---------------------------------------------------------------------------

COMPOSE = Path(__file__).resolve().parents[4] / "deploy" / "docker-compose.yml"


def _compose() -> dict:
    yaml = pytest.importorskip("yaml", reason="pyyaml not installed")
    return yaml.safe_load(COMPOSE.read_text())


def test_schema_owner_is_deployed_alongside_meeting_api() -> None:
    """meeting-api ships no alembic and never calls create_all.

    ensure_schema() lives in admin-api and its Base declares meetings /
    transcriptions / meeting_sessions. Without admin-api the database stays empty
    and meeting-api has nothing to query.
    """
    services = _compose()["services"]
    assert "vexa12-admin-api" in services, (
        "vexa12-admin-api owns ensure_schema() for the meetings tables — dropping it leaves vexa_v012 empty."
    )
    deps = services["vexa12-meeting-api"]["depends_on"]
    assert deps.get("vexa12-admin-api", {}).get("condition") == "service_healthy", (
        "meeting-api must wait for the schema owner to be healthy before its first query"
    )


def test_redis_is_not_shared_with_another_vexa_stack() -> None:
    """Redis pub/sub is instance-wide — a DB index does NOT isolate channels.

    Vexa publishes `bm:meeting:{id}:status` and `bot_commands:meeting:{id}` with
    meeting ids drawn from its own database. Any second Vexa deployment sharing this
    Redis instance would collide on those names even on a different DB index, and
    Vexa exposes no channel prefix. The 0.10 stack this once guarded against is gone
    (SPEC-VEXA-004); the invariant stays because the next parallel stack would
    reintroduce the hazard.
    """
    services = _compose()["services"]
    vexa_hosts = {
        name: svc["environment"]["REDIS_URL"].split("@", 1)[1].split(":", 1)[0]
        for name, svc in services.items()
        if "REDIS_URL" in svc.get("environment", {}) and name.startswith("vexa")
    }
    assert vexa_hosts, "no Vexa service declares a REDIS_URL — did the stack move?"
    assert set(vexa_hosts.values()) == {"vexa12-redis"}, (
        f"Vexa services point at more than one Redis host: {vexa_hosts}"
    )

    others = {
        name: svc["environment"]["REDIS_URL"]
        for name, svc in services.items()
        if "REDIS_URL" in svc.get("environment", {}) and not name.startswith("vexa")
    }
    for name, url in others.items():
        assert "vexa12-redis" not in url, (
            f"{name} shares the Vexa Redis instance; its pub/sub channels are not isolated"
        )


def test_bot_image_is_pinned_and_flagged_as_a_pull_prerequisite() -> None:
    """docker-socket-proxy has IMAGES disabled, so the runtime cannot pull the bot.

    The bot image is an env value rather than a compose service, so `compose up`
    does not fetch it either. It must be pre-pulled on the host; this test keeps the
    requirement attached to the tag it applies to.
    """
    services = _compose()["services"]
    image = services["vexa12-runtime"]["environment"]["BROWSER_IMAGE"]
    assert image.startswith("vexaai/vexa-bot:"), image
    assert image.rsplit(":", 1)[1] not in ("latest", "dev", "staging"), "bot image must be immutably pinned"

    # Assert the EXACT ref, not just the prefix: a tag bump that forgets the pull
    # instruction is precisely the drift this test exists to catch, and a
    # prefix-only match would sail straight past it.
    body = COMPOSE.read_text()
    assert f"docker pull {image}" in body, (
        f"BROWSER_IMAGE is {image} but the documented pre-pull instruction does not name that "
        "exact ref. The runtime cannot fetch the image itself (IMAGES disabled in "
        "docker-socket-proxy), so a stale instruction means the first spawn fails."
    )
    script = COMPOSE.resolve().parents[1] / "scripts" / "check-vexa12-deploy-preconditions.sh"
    assert f'BOT_IMAGE="{image}"' in script.read_text(), (
        f"check-vexa12-deploy-preconditions.sh must check for {image}, the tag actually deployed"
    )


def test_meeting_api_is_not_on_a_shared_application_network() -> None:
    """meeting-api's network membership IS its authorization boundary.

    Vexa 0.12's meeting-api has no auth of its own: it trusts the X-User-Id header
    the gateway would normally set (bot_spawn/router.py::_resolve_user_id accepts any
    integer) and falls back to AllowAllServiceAuthority when no service-authority
    config is present — which is Klai's configuration. Whatever network it sits on can
    therefore read any tenant's transcripts and stop or spawn bots with a forged header.

    It was briefly on klai-net, which carries ~70 containers including ~42 tenant
    LibreChat instances. An adversarial review proved the spoof from an unrelated
    container. Its network must stay a closed two-party link with portal-api.
    """
    compose = _compose()
    services = compose["services"]
    meeting_nets = set(services["vexa12-meeting-api"]["networks"])

    shared = {"klai-net", "net-redis", "net-mongodb", "net-meilisearch", "socket-proxy"}
    assert not (meeting_nets & shared), (
        f"vexa12-meeting-api is on shared network(s) {sorted(meeting_nets & shared)}; any "
        "container there can forge X-User-Id and read every tenant's transcripts"
    )

    link = meeting_nets & set(services["portal-api"]["networks"])
    link.discard("net-postgres")  # both need the DB; that is not the API link
    assert link, "portal-api has no dedicated network in common with vexa12-meeting-api"

    for net in link:
        members = sorted(n for n, v in services.items() if net in (v.get("networks") or {}))
        assert members == ["portal-api", "vexa12-meeting-api"], (
            f"network {net!r} must have exactly portal-api + vexa12-meeting-api, has {members}"
        )
        assert compose["networks"][net].get("internal") is True, f"{net!r} must be internal"


def test_admin_api_is_not_reachable_from_the_bot_network() -> None:
    """Bot containers are the least-trusted thing in the stack.

    They run Chromium inside arbitrary external meetings. admin-api holds the
    identity/token surface, and the 0.10 stack deliberately keeps its admin-api off
    vexa-bots. meeting-api reaches it over a dedicated internal link instead.
    """
    services = _compose()["services"]
    admin_nets = set(services["vexa12-admin-api"]["networks"])
    bot_net = "vexa12-bots"

    assert bot_net not in admin_nets, (
        f"vexa12-admin-api is on {bot_net}; a spawned bot could reach it on :8001. "
        "Use the vexa12-internal link (meeting-api <-> admin-api) instead."
    )
    link = admin_nets & set(services["vexa12-meeting-api"]["networks"])
    assert link, "meeting-api has no network in common with admin-api — the schema owner is unreachable"
    for net in link:
        assert _compose()["networks"][net].get("internal") is True, (
            f"the meeting-api <-> admin-api link runs over {net!r}, which is not internal"
        )


def test_meeting_api_and_admin_api_share_the_same_admin_token() -> None:
    """MeetingToken is signed by meeting-api and verified by admin-api.

    Two different values fail at verification time, not at boot — so nothing goes red
    until a real meeting tries to upload. Locking the pair here makes a typo a test
    failure instead of a runtime mystery.
    """
    services = _compose()["services"]
    signer = services["vexa12-meeting-api"]["environment"]["ADMIN_TOKEN"]
    verifier = services["vexa12-admin-api"]["environment"]["ADMIN_API_TOKEN"]
    assert signer == verifier, f"meeting-api signs MeetingToken with {signer} but admin-api verifies with {verifier}"


def test_vexa_secrets_are_dedicated_to_the_vexa_stack() -> None:
    """The Vexa deployment is its own trust boundary.

    It once ran beside a second Vexa stack and the rule was "do not share a secret
    across the two" (SPEC-VEXA-004). With the old stack removed the rule generalises:
    the Vexa admin token and internal secret must not be reused by any other klai
    service, so a compromise there does not hand over bot orchestration.
    """
    services = _compose()["services"]
    vexa_secrets = set()
    for name, svc in services.items():
        if not name.startswith("vexa12"):
            continue
        for key in ("ADMIN_TOKEN", "ADMIN_API_TOKEN", "INTERNAL_API_SECRET"):
            if value := svc.get("environment", {}).get(key):
                vexa_secrets.add(value)
    assert vexa_secrets, "the Vexa stack declares no secrets — did the env keys move?"

    for name, svc in services.items():
        if name.startswith("vexa12"):
            continue
        for key, value in (svc.get("environment") or {}).items():
            assert value not in vexa_secrets, f"{name}.{key} reuses a Vexa stack secret ({value})"


# ---------------------------------------------------------------------------
# 6. Live smoke — opt-in, needs a running meeting-api
# ---------------------------------------------------------------------------


@pytest.mark.vexa_live
@pytest.mark.anyio
async def test_live_meeting_api_serves_the_klai_surface() -> None:
    """Read-only probe against a real meeting-api.

    Run with:
        VEXA_CONTRACT_BASE_URL=http://localhost:18080 \
        VEXA_CONTRACT_API_KEY=... \
        uv run pytest tests/contract -m vexa_live

    Only GET routes are probed — this must never spawn a bot.
    """
    import httpx

    base_url = os.getenv("VEXA_CONTRACT_BASE_URL")
    if not base_url:
        pytest.skip("VEXA_CONTRACT_BASE_URL not set")

    headers = {"X-User-Id": os.getenv("VEXA_CONTRACT_USER_ID", "1")}
    if api_key := os.getenv("VEXA_CONTRACT_API_KEY"):
        headers["X-API-Key"] = api_key

    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=15.0) as client:
        health = await client.get("/health")
        assert health.status_code == 200, f"meeting-api /health returned {health.status_code}"

        status = await client.get("/bots/status")
        assert status.status_code == 200, f"GET /bots/status returned {status.status_code}"
        assert "running_bots" in status.json(), "GET /bots/status lost its running_bots key"
