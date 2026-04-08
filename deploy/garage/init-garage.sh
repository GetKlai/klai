#!/usr/bin/env sh
# Bootstrap Garage: assign layout, create bucket, enable website, create access key.
# Designed to run via `docker exec` on the garage container (has /garage CLI).
# Usage: docker exec klai-core-garage-1 sh /opt/garage-init.sh
set -eu

BUCKET="${GARAGE_BUCKET:-klai-images}"

echo "=== Garage Bootstrap ==="

# 1. Get node ID
NODE_ID=$(/garage status 2>/dev/null | grep -oE '[a-f0-9]{16}' | head -1)
if [ -z "${NODE_ID}" ]; then
    echo "ERROR: Could not determine node ID"
    exit 1
fi
echo "Node ID: ${NODE_ID}"

# 2. Assign layout (skip if already assigned)
CURRENT_CAPACITY=$(/garage layout show 2>/dev/null | grep "${NODE_ID}" | grep -c "1000.0 MB" || true)
if [ "${CURRENT_CAPACITY}" -eq 0 ]; then
    echo "Assigning layout..."
    /garage layout assign -z default -c 1G "${NODE_ID}"
    /garage layout apply --version 1
    echo "Layout applied."
else
    echo "Layout already assigned, skipping."
fi

# 3. Create bucket (skip if exists)
BUCKET_EXISTS=$(/garage bucket list 2>/dev/null | grep -c "${BUCKET}" || true)
if [ "${BUCKET_EXISTS}" -eq 0 ]; then
    echo "Creating bucket '${BUCKET}'..."
    /garage bucket create "${BUCKET}"
    echo "Bucket created."
else
    echo "Bucket '${BUCKET}' already exists."
fi

# 4. Enable website access (anonymous reads via web endpoint)
echo "Enabling website access on '${BUCKET}'..."
/garage bucket website --allow "${BUCKET}" 2>/dev/null || true

# 5. Create access key (skip if env var already set)
if [ -z "${GARAGE_ACCESS_KEY:-}" ]; then
    echo "Creating access key..."
    /garage key create klai-connector
    echo ""
    echo "IMPORTANT: Copy the Key ID and Secret key above into your .env.sops!"
    echo "Set GARAGE_ACCESS_KEY and GARAGE_SECRET_KEY."

    # Grant key access to bucket
    /garage bucket allow --read --write --owner "${BUCKET}" --key klai-connector
    echo "Key granted RWO on '${BUCKET}'."
else
    echo "GARAGE_ACCESS_KEY already set, skipping key creation."
fi

echo "=== Garage Bootstrap Complete ==="
