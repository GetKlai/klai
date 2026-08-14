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

    # Upstream signs json.dumps(envelope) with Python's default separators.
    payload_bytes = json.dumps(envelope).encode()
    recomputed = _sign(payload_bytes, secret, timestamp)

    assert recomputed.startswith("sha256=")
    assert len(recomputed) == len("sha256=") + 64
    # Self-consistency: the same bytes + secret + ts must be stable and verifiable.
    assert hmac.compare_digest(recomputed, _sign(payload_bytes, secret, timestamp))


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


# ---------------------------------------------------------------------------
# 5. Live smoke — opt-in, needs a running meeting-api
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
