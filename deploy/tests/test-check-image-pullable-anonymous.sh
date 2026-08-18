#!/bin/sh
set -eu

TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/klai-image-check-test.XXXXXX")
trap 'rm -rf "$TEST_DIR"' EXIT HUP INT TERM

mkdir -p "$TEST_DIR/bin" "$TEST_DIR/authenticated"
cp deploy/tests/fixtures/fake-docker-anonymous-check "$TEST_DIR/bin/docker"
chmod +x "$TEST_DIR/bin/docker"
printf '{"auths":{"registry.example":{"auth":"must-not-be-used"}}}\n' \
    > "$TEST_DIR/authenticated/config.json"

export EXPECTED_AUTH_CONFIG="$TEST_DIR/authenticated"
export DOCKER_CONFIG="$EXPECTED_AUTH_CONFIG"
export DOCKER_AUTH_CONFIG='{"auths":{"registry.example":{"auth":"also-must-not-be-used"}}}'
export DOCKER_CALL_LOG="$TEST_DIR/docker-calls"
PATH="$TEST_DIR/bin:$PATH" sh deploy/check-image-pullable.sh

[ -s "$DOCKER_CALL_LOG" ]
while IFS= read -r used_config; do
    [ "$used_config" != "$EXPECTED_AUTH_CONFIG" ]
done < "$DOCKER_CALL_LOG"

echo "anonymous image pullability isolation: OK"
