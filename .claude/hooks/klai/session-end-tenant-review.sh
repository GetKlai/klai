#!/usr/bin/env bash
# Stop-hook: suggest /klai:tenant-review at session end IF the working tree
# has uncommitted changes that touch tenant-relevant paths.
#
# Suggestion-only — does NOT block. The hook prints a one-line nudge.
# User runs /klai:tenant-review manually if interested.
#
# Tenant-relevant paths (matches .claude/skills/klai/tenant-isolation-checks/SKILL.md):
# - klai-*/{,**/}{models,api,routes,services}/**.py
# - klai-*/{,**/}alembic/versions/**.{py,sql}
# - klai-*/{,**/}config.py + core/config.py
# - klai-*/{,**/}database.py + core/database.py
# - klai-libs/{webhook-replay,identity-assert,connector-credentials}/**
# - deploy/caddy/Caddyfile
# - klai-infra/core-01/.env.sops
#
# Exit codes:
#   0 — always (suggestion-only, never block session end)
#
# Disable: set KLAI_TENANT_REVIEW_SUGGESTION=0 in env.

set -u

# Skip if disabled
if [ "${KLAI_TENANT_REVIEW_SUGGESTION:-1}" = "0" ]; then
    exit 0
fi

# Only run in klai repo
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
    exit 0
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
if [ ! -d "$REPO_ROOT/.moai" ] || [ ! -d "$REPO_ROOT/klai-portal" ]; then
    # Not the klai repo
    exit 0
fi

cd "$REPO_ROOT" || exit 0

# Check for uncommitted changes touching tenant-relevant paths
TENANT_PATHS_REGEX='^.*/(models|api|routes|services|alembic|core)/.*\.(py|sql)$|^.*/(config|database)\.py$|^klai-libs/(webhook-replay|identity-assert|connector-credentials)/|^deploy/caddy/Caddyfile$|/post_deploy_.*\.sql$'

# Combine staged + unstaged + untracked, filter to tenant paths
TENANT_FILES=$(
    {
        git diff --name-only HEAD 2>/dev/null
        git diff --name-only --cached 2>/dev/null
        git ls-files --others --exclude-standard 2>/dev/null
    } | sort -u | grep -E "$TENANT_PATHS_REGEX" 2>/dev/null
)

# Also check for committed-but-not-pushed changes on a feature branch
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ -n "$BRANCH" ] && [ "$BRANCH" != "main" ] && [ "$BRANCH" != "HEAD" ]; then
    UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null)
    if [ -z "$UPSTREAM" ]; then
        # No upstream — compare against main
        UNPUSHED_TENANT=$(git diff --name-only main..HEAD 2>/dev/null | grep -E "$TENANT_PATHS_REGEX" 2>/dev/null)
        if [ -n "$UNPUSHED_TENANT" ]; then
            TENANT_FILES="$TENANT_FILES
$UNPUSHED_TENANT"
        fi
    fi
fi

# Strip blanks
TENANT_FILES=$(echo "$TENANT_FILES" | sed '/^$/d' | sort -u)

if [ -z "$TENANT_FILES" ]; then
    # Nothing tenant-relevant changed
    exit 0
fi

# Count
COUNT=$(echo "$TENANT_FILES" | wc -l | tr -d ' ')

# Print suggestion
echo ""
echo "🔒 [klai-tenant-review] $COUNT tenant-relevant file(s) changed in this session."
echo "   Run /klai:tenant-review to check against standards.md before push/merge."
echo "   (Disable: set KLAI_TENANT_REVIEW_SUGGESTION=0)"

exit 0
