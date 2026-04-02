#!/usr/bin/env python3
"""Klai Confidence Stop Hook.

Blocks agent from stopping without evidence-based confidence reporting.
Registered in .claude/settings.json as a Stop hook.

SPEC: SPEC-CONFIDENCE-001 (REQ-1, REQ-2)
Cross-platform: works on macOS, Linux, and Windows.
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone


LOG_PATH = os.path.join(tempfile.gettempdir(), "klai-confidence-hook.log")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}))


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = {}

    stop_hook_active = str(data.get("stop_hook_active", "false")).lower()
    last_message = data.get("last_assistant_message", "")
    transcript_path = data.get("transcript_path", "")

    # --- Anti-loop protection (AC-1.4) ---
    if stop_hook_active == "true":
        log("ALLOW: stop_hook_active=true (anti-loop)")
        return

    # --- Completion signal detection ---
    # Only enforce confidence on completion claims, not conversational turns.
    # Without this, the hook blocks every turn pause (e.g. waiting for user input).
    completion_pattern = re.compile(
        r"confidence:\s*\d+|"
        r"\b(done|complete[d]?|finished|klaar|afgerond)\b|"
        r"\b(implemented|committed|pushed|deployed|fixed|resolved)\b|"
        r"\btask.*(complete|done|finished)\b",
        re.IGNORECASE,
    )
    if not completion_pattern.search(last_message):
        log("ALLOW: no completion signal in last message (conversational turn)")
        return

    # --- Build search text: last message + transcript tail ---
    search_text = last_message
    if transcript_path and os.path.isfile(transcript_path):
        try:
            with open(transcript_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            search_text += " " + "".join(lines[-50:])
        except OSError:
            pass

    # --- REQ-1: Check for confidence number (AC-1.3) ---
    # Patterns: "Confidence: 85", "confidence: 85", "Confidence 85", "85/100"
    confidence_num = None

    matches = re.findall(r"[Cc]onfidence:?\s*(\d+)", search_text)
    if matches:
        confidence_num = int(matches[-1])
    else:
        ratio_matches = re.findall(r"(\d+)/100", search_text)
        if ratio_matches:
            confidence_num = int(ratio_matches[-1])

    if confidence_num is None:
        log("BLOCK: no confidence number found")
        block(
            "Report your confidence level before stopping. "
            "Format: Confidence: [0-100] \u2014 [evidence summary]"
        )
        return

    log(f"Found confidence: {confidence_num}")

    # --- REQ-2: Adversarial check at >= 80 (AC-2.1, AC-2.2, AC-2.3) ---
    if confidence_num >= 80:
        adversarial_pattern = re.compile(
            r"bug|could.*(wrong|fail|break)|risk|"
            r"issue.*(found|remain)|adversarial|"
            r"checked for|no issues|reviewed for",
            re.IGNORECASE,
        )
        if not adversarial_pattern.search(search_text):
            log(f"BLOCK: confidence >= 80 ({confidence_num}) without adversarial check")
            block(
                "Confidence >= 80 requires adversarial self-check. "
                "Ask yourself: what bugs can I find in what I just did?"
            )
            return
        log(f"PASS: adversarial check found at confidence {confidence_num}")

    # --- All checks passed ---
    log(f"ALLOW: confidence={confidence_num}, all checks passed")


if __name__ == "__main__":
    main()
