#!/usr/bin/env bash
# Bootstrap Garage: assign layout, create bucket, create access key.
# Runs as a one-shot init container after Garage starts.
set -euo pipefail

GARAGE_ADMIN="http://garage:3903"
ADMIN_TOKEN="${GARAGE_ADMIN_TOKEN:?GARAGE_ADMIN_TOKEN is required}"
BUCKET="${GARAGE_BUCKET:-klai-images}"

header=(-H "Authorization: Bearer ${ADMIN_TOKEN}")

echo "Waiting for Garage admin API..."
for i in $(seq 1 30); do
    if curl -sf "${header[@]}" "${GARAGE_ADMIN}/v1/status" >/dev/null 2>&1; then
        echo "Garage is ready."
        break
    fi
    sleep 1
done

# Get node ID from status.
NODE_ID=$(curl -sf "${header[@]}" "${GARAGE_ADMIN}/v1/status" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data['node'])
")
echo "Node ID: ${NODE_ID}"

# Check if layout is already applied (node has a role).
HAS_ROLE=$(curl -sf "${header[@]}" "${GARAGE_ADMIN}/v1/layout" | python3 -c "
import json, sys
data = json.load(sys.stdin)
roles = data.get('stagedRoleChanges', []) + [r for r in data.get('roles', []) if r.get('zone')]
print('yes' if roles else 'no')
" 2>/dev/null || echo "no")

if [ "${HAS_ROLE}" = "no" ]; then
    echo "Assigning layout..."
    curl -sf "${header[@]}" -X POST "${GARAGE_ADMIN}/v1/layout" \
        -H "Content-Type: application/json" \
        -d "[{\"id\": \"${NODE_ID}\", \"zone\": \"default\", \"capacity\": 1073741824}]"

    # Get current layout version and apply.
    VERSION=$(curl -sf "${header[@]}" "${GARAGE_ADMIN}/v1/layout" | python3 -c "
import json, sys
print(json.load(sys.stdin).get('version', 0) + 1)
")
    curl -sf "${header[@]}" -X POST "${GARAGE_ADMIN}/v1/layout/apply" \
        -H "Content-Type: application/json" \
        -d "{\"version\": ${VERSION}}"
    echo "Layout applied (version ${VERSION})."
else
    echo "Layout already assigned, skipping."
fi

# Create bucket if it doesn't exist.
EXISTING=$(curl -sf "${header[@]}" "${GARAGE_ADMIN}/v1/bucket?globalAlias=${BUCKET}" 2>/dev/null || true)
if [ -z "${EXISTING}" ] || echo "${EXISTING}" | grep -q '"code"'; then
    echo "Creating bucket '${BUCKET}'..."
    curl -sf "${header[@]}" -X POST "${GARAGE_ADMIN}/v1/bucket" \
        -H "Content-Type: application/json" \
        -d "{\"globalAlias\": \"${BUCKET}\"}"
    echo "Bucket created."
else
    echo "Bucket '${BUCKET}' already exists."
fi

# Create access key if not already set in env.
if [ -z "${GARAGE_ACCESS_KEY:-}" ]; then
    echo "Creating access key..."
    KEY_RESPONSE=$(curl -sf "${header[@]}" -X POST "${GARAGE_ADMIN}/v1/key" \
        -H "Content-Type: application/json" \
        -d "{\"name\": \"klai-connector\"}")
    ACCESS_KEY=$(echo "${KEY_RESPONSE}" | python3 -c "import json,sys; print(json.load(sys.stdin)['accessKeyId'])")
    SECRET_KEY=$(echo "${KEY_RESPONSE}" | python3 -c "import json,sys; print(json.load(sys.stdin)['secretAccessKey'])")

    # Grant read+write on the bucket.
    KEY_ID=$(echo "${KEY_RESPONSE}" | python3 -c "import json,sys; print(json.load(sys.stdin)['accessKeyId'])")
    BUCKET_ID=$(curl -sf "${header[@]}" "${GARAGE_ADMIN}/v1/bucket?globalAlias=${BUCKET}" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
    curl -sf "${header[@]}" -X POST "${GARAGE_ADMIN}/v1/bucket/allow" \
        -H "Content-Type: application/json" \
        -d "{\"bucketId\": \"${BUCKET_ID}\", \"accessKeyId\": \"${KEY_ID}\", \"permissions\": {\"read\": true, \"write\": true, \"owner\": true}}"

    echo "============================================"
    echo "GARAGE_ACCESS_KEY=${ACCESS_KEY}"
    echo "GARAGE_SECRET_KEY=${SECRET_KEY}"
    echo "============================================"
    echo "Add these to your .env file!"
else
    echo "GARAGE_ACCESS_KEY already set, skipping key creation."
fi

echo "Garage bootstrap complete."
