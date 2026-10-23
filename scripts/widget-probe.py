#!/usr/bin/env python3
"""Synthetic probe: does a customer's widget get a good answer right now?

On 2026-10-23 the widget answered "Er ging iets mis" from about 03:00 to 10:40
UTC (a FalkorDB crash loop stalled retrieval) and the first signal was a
customer. This probe walks the same two calls a customer's browser makes, with
the same headers and body (klai-widget/src/api/widget-config.ts and
chat-stream.ts):

  1. GET  /partner/v1/widget-config?id=<widget id>   (Origin + session id) -> session_token
  2. POST <chat_endpoint> with Bearer <session_token>, stream=true          -> SSE

and appends one JSON verdict line to stdout and to LOG_FILE, which Alloy ships
to VictoriaLogs as service:widget-probe (deploy/alloy/config.alloy). The line
is the only output: the widget id and the session token are never printed.

ok needs all of: HTTP 2xx, no error frame, a non-empty answer, at least one
source, and a total time under MAX_LATENCY_MS. A fixed "Er ging iets mis" reply
is HTTP 200 with no sources, so the source check is what catches it. reason
names the first failing check, in this order: config, unreachable, timeout,
http, mint_response, stream_error, empty_answer, no_sources, latency.

Latency threshold: 30000 ms. Measured 2026-10-23 from VictoriaLogs
(service:portal-api event:partner_chat_turn_timing, total_ms): over the last 7
days p50 3.7 s, p95 18.7 s (527 turns, including the 03:00-10:40 incident);
over the 7 days before the incident p95 11.9 s. 30 s is 2.5x the healthy p95
and above the incident-inflated one, so a normal slow turn does not page but a
retrieval stall does. The measured time includes the token mint (about 100 ms).

Needs from /opt/klai/.env (or the process environment, which wins):
  WIDGET_PROBE_WIDGET_ID   the probe widget's wgt_... id (required)
  WIDGET_PROBE_ORIGIN      an origin in that widget's allowed_origins
                           (default https://probe.example.com)
  WIDGET_PROBE_QUESTION    a question the probe tenant's KB answers
  WIDGET_PROBE_BASE_URL, WIDGET_PROBE_MAX_LATENCY_MS, WIDGET_PROBE_TIMEOUT,
  WIDGET_PROBE_LOG_FILE    optional overrides; see DEFAULTS below.
Provisioning: docs/runbooks/widget-probe.md.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone

DEFAULTS = {
    "WIDGET_PROBE_BASE_URL": "https://api.getklai.com",
    "WIDGET_PROBE_ORIGIN": "https://probe.example.com",
    "WIDGET_PROBE_QUESTION": "Wat zijn de openingstijden van het supportteam van Probe Bakkerij?",
    "WIDGET_PROBE_MAX_LATENCY_MS": "30000",
    # Per socket read, so a stalled stream ends the run. The unit allows 90 s.
    "WIDGET_PROBE_TIMEOUT": "45",
    "WIDGET_PROBE_LOG_FILE": "/opt/klai/logs/widget-probe.log",
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(os.environ.get("KLAI_ENV_FILE", "/opt/klai/.env")) as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key.startswith("WIDGET_PROBE_"):
                    cfg[key] = value.strip("'\"")
    except OSError:
        pass
    cfg.update({k: v for k, v in os.environ.items() if k.startswith("WIDGET_PROBE_")})
    return cfg


class Fail(Exception):
    def __init__(self, reason, http_status=0):
        self.reason, self.http_status = reason, http_status


def request(req, timeout):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raise Fail("http", e.code)
    except urllib.error.URLError as e:
        raise Fail("timeout" if isinstance(e.reason, TimeoutError) else "unreachable")
    except TimeoutError:
        raise Fail("timeout")
    except OSError:
        raise Fail("unreachable")


def probe(cfg):
    """Return (http_status, answer, source_count); raise Fail on the first failing check."""
    base, timeout = cfg["WIDGET_PROBE_BASE_URL"], float(cfg["WIDGET_PROBE_TIMEOUT"])
    query = urllib.parse.urlencode({"id": cfg["WIDGET_PROBE_WIDGET_ID"]})
    mint = urllib.request.Request(
        f"{base}/partner/v1/widget-config?{query}",
        headers={
            "Origin": cfg["WIDGET_PROBE_ORIGIN"],
            "X-Klai-Widget-Session-Id": uuid.uuid4().hex,
        },
    )
    with request(mint, timeout) as resp:
        try:
            config = json.load(resp)
            token, endpoint = config["session_token"], config["chat_endpoint"]
        except (ValueError, KeyError, TypeError):
            raise Fail("mint_response", resp.status)
    if endpoint.startswith("/"):
        endpoint = base + endpoint

    chat = urllib.request.Request(
        endpoint,
        data=json.dumps(
            {
                "messages": [{"role": "user", "content": cfg["WIDGET_PROBE_QUESTION"]}],
                "stream": True,
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    answer, sources = [], 0
    with request(chat, timeout) as resp:
        status = resp.status
        try:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("error"):
                    raise Fail("stream_error", status)
                delta = (event.get("choices") or [{}])[0].get("delta") or {}
                answer.append(delta.get("content") or "")
                if isinstance(delta.get("sources"), list):
                    sources += len(delta["sources"])
        except TimeoutError:
            raise Fail("timeout", status)
    return status, "".join(answer), sources


def main():
    cfg = load_config()
    started = time.monotonic()
    http_status, answer, sources, reason = 0, "", 0, "ok"
    if not cfg.get("WIDGET_PROBE_WIDGET_ID"):
        reason = "config"
    else:
        try:
            http_status, answer, sources = probe(cfg)
        except Fail as f:
            reason, http_status = f.reason, f.http_status
    latency_ms = round((time.monotonic() - started) * 1000)
    if reason == "ok":
        if not answer.strip():
            reason = "empty_answer"
        elif sources == 0:
            reason = "no_sources"
        elif latency_ms > int(cfg["WIDGET_PROBE_MAX_LATENCY_MS"]):
            reason = "latency"
    line = json.dumps(
        {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "service": "widget-probe",
            "event": "widget_probe",
            "msg": "widget_probe",
            "status": "ok" if reason == "ok" else "fail",
            "reason": reason,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "has_sources": sources > 0,
            "answer_chars": len(answer.strip()),
        },
        separators=(",", ":"),
    )
    print(line)
    log_file = cfg["WIDGET_PROBE_LOG_FILE"]
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, "a") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())
