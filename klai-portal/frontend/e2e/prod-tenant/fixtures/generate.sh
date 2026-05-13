#!/usr/bin/env bash
# Regenerate the audio fixture for J06 (scribe upload + transcribe).
#
# Why a script and not a committed binary: the .wav is small but binary
# is awkward in PRs. This script lets reviewers see exactly what audio
# is being produced. CI can regenerate on first run if .wav is missing.
#
# Requirements: ffmpeg, espeak (or `say` on macOS as fallback).
#
# Usage:
#   cd klai-portal/frontend/e2e/prod-tenant/fixtures
#   bash generate.sh

set -euo pipefail

cd "$(dirname "$0")"

OUT="e2e-fixture.wav"
TEXT="test test test test"

if command -v espeak >/dev/null 2>&1; then
    echo "[fixtures] generating $OUT via espeak..."
    espeak -w /tmp/e2e-raw.wav "$TEXT"
elif command -v say >/dev/null 2>&1; then
    echo "[fixtures] generating $OUT via macOS 'say'..."
    say -o /tmp/e2e-raw.aiff "$TEXT"
    ffmpeg -y -i /tmp/e2e-raw.aiff /tmp/e2e-raw.wav
else
    echo "[fixtures] no TTS engine found (espeak / say)" >&2
    echo "[fixtures] install espeak: 'apt-get install -y espeak' or 'brew install espeak'" >&2
    exit 1
fi

# Normalise to 16kHz mono PCM — what most ASR pipelines expect.
ffmpeg -y -i /tmp/e2e-raw.wav -ac 1 -ar 16000 -sample_fmt s16 "$OUT"
rm -f /tmp/e2e-raw.wav /tmp/e2e-raw.aiff

echo "[fixtures] $OUT ready ($(du -h "$OUT" | awk '{print $1}'))"
