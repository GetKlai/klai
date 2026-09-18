#!/usr/bin/env bash
# Regression test: a full disk must stop the deploy before it touches the schema.
#
# 2026-09-18. The root filesystem was at 100%, `docker pull` and `alembic
# upgrade head` both succeeded, and the deploy died at `mktemp -d` with ENOSPC —
# migrations applied, post-deploy SQL not. The guard has to fire before the
# first docker call, so the failure is a no-op instead of a half-deploy.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/deploy/scripts/deploy-portal-api.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

COMPOSE_DIR="$TMP/compose"
mkdir -p "$COMPOSE_DIR" "$TMP/bin"

# Any docker invocation means the guard let the deploy start.
cat >"$TMP/bin/docker" <<STUB
#!/usr/bin/env bash
echo "\$*" >>"$TMP/docker-was-called"
exit 0
STUB

# df is the only thing the guard reads, so the disk state is what we vary. It
# queries three paths, so the stub answers with three data lines.
# 1 GiB free everywhere, below the 4 GiB floor.
cat >"$TMP/bin/df" <<'STUB'
#!/usr/bin/env bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/md2 456519844 455471268 1048576 100% /"
echo "/dev/md2 456519844 455471268 1048576 100% /"
echo "/dev/md2 456519844 455471268 1048576 100% /"
STUB
chmod +x "$TMP/bin/docker" "$TMP/bin/df"

set +e
PATH="$TMP/bin:$PATH" KLAI_COMPOSE_DIR="$COMPOSE_DIR" \
    bash "$SCRIPT" >"$TMP/out" 2>&1
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
    echo "FAIL: deploy reported success with 1 GiB free" >&2
    sed 's/^/  /' "$TMP/out" >&2
    exit 1
fi

if [ -f "$TMP/docker-was-called" ]; then
    echo "FAIL: guard ran too late — docker was invoked on a full disk:" >&2
    sed 's/^/  /' "$TMP/docker-was-called" >&2
    exit 1
fi

# One tight filesystem is enough to stop the deploy. The image store filling
# while /opt/klai has room is the shape this actually takes: on core-01 the
# 224 GB of images and the 120 GB of backups sat on one device, but nothing
# guarantees that, and a guard that reads only the compose directory would wave
# the deploy through while the pull has nowhere to land.
cat >"$TMP/bin/df" <<'STUB'
#!/usr/bin/env bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/md2 456519844 403000000 53519844 89% /"
echo "/dev/md2 456519844 403000000 53519844 89% /"
echo "/dev/md3 456519844 455471268 2097152 100% /var/lib/containerd"
STUB

set +e
PATH="$TMP/bin:$PATH" KLAI_COMPOSE_DIR="$COMPOSE_DIR" \
    bash "$SCRIPT" >"$TMP/out2" 2>&1
rc=$?
set -e

if [ "$rc" -eq 0 ] || [ -f "$TMP/docker-was-called" ]; then
    echo "FAIL: guard read only the compose directory — 2 GiB on the image store let the deploy start" >&2
    sed 's/^/  /' "$TMP/out2" >&2
    exit 1
fi

# Room everywhere: the guard must get out of the way. The deploy goes on to fail
# on the stubbed compose project, which is fine — it proves the guard passed.
cat >"$TMP/bin/df" <<'STUB'
#!/usr/bin/env bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/md2 456519844 403000000 53519844 89% /"
echo "/dev/md2 456519844 403000000 53519844 89% /"
echo "/dev/md2 456519844 403000000 53519844 89% /"
STUB

set +e
PATH="$TMP/bin:$PATH" KLAI_COMPOSE_DIR="$COMPOSE_DIR" \
    bash "$SCRIPT" >"$TMP/out3" 2>&1
set -e

if ! [ -f "$TMP/docker-was-called" ]; then
    echo "FAIL: guard blocked a deploy with 51 GiB free everywhere" >&2
    sed 's/^/  /' "$TMP/out3" >&2
    exit 1
fi

echo "deploy-portal-api disk guard: OK"
