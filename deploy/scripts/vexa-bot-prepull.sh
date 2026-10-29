#!/bin/bash
# Pull the Vexa bot image named in a compose file onto this host.
#
# The bot image is an ENV value (BROWSER_IMAGE on vexa12-runtime), not a compose
# service, and docker-socket-proxy has IMAGES disabled (SPEC-SEC-024), so neither
# `compose up` nor the runtime ever fetches it. Without this step a bumped tag
# reaches the host as a runtime that spawns an image the host does not have, and
# the first meeting fails with "No such image".
#
# deploy-compose.yml runs this on the clone of the commit being deployed, before
# the first file on /opt/klai is replaced. A tag that does not exist therefore
# ends the sync red with the host untouched. Idempotent: an image that is
# already present is not pulled again.
#
# usage: vexa-bot-prepull.sh <compose-file>

set -euo pipefail

if [[ $# -ne 1 || ! -r "$1" ]]; then
    echo "usage: $0 <compose-file>" >&2
    exit 2
fi

image="$(sed -n 's/^[[:space:]]*BROWSER_IMAGE:[[:space:]]*\(vexaai\/vexa-bot:[A-Za-z0-9._-]*\)[[:space:]]*$/\1/p' "$1")"
if [[ -z "$image" || "$image" == *$'\n'* ]]; then
    echo "ERROR: expected exactly one literal 'BROWSER_IMAGE: vexaai/vexa-bot:<tag>' in $1" >&2
    exit 1
fi

if docker image inspect "$image" >/dev/null 2>&1; then
    echo "Vexa bot image already present: $image"
    exit 0
fi

docker pull "$image"
echo "Vexa bot image pulled: $image"
