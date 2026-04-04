---
id: SPEC-GPU-001
document: plan
version: "2.1.0"
status: done
created: "2026-03-27"
updated: "2026-03-29"
---

# SPEC-GPU-001: Implementation Plan — GPU Inference Service Migration to gpu-01

## Overview

Five-phase migration of GPU inference services from core-01 to a dedicated Hetzner server (gpu-01, GEX44, `5.9.10.215`). Starts with OS installation including LUKS full-disk encryption, then Docker setup, SSH tunnel configuration, consumer migration, and monitoring.

---

## Phase 0: OS Installation with LUKS Encryption (gpu-01)

**Priority: MUST COMPLETE FIRST — gates everything else**

### Milestone 0.1: Boot into Rescue System

```bash
# In Hetzner Robot: Servers → #2963286 → Rescue → Linux 64-bit → Activate
# Then reboot the server
# SSH into rescue system:
ssh root@5.9.10.215
```

### Milestone 0.2: Run Installimage with LUKS

```bash
installimage
```

In the Installimage TUI, configure:
- **OS**: Ubuntu 24.04 LTS (or Debian 12)
- **Partitioning**: Enable encryption (LUKS) — Installimage will prompt for passphrase
- **Root partition**: encrypted LUKS container
- Recommended partition layout:
  ```
  PART  /boot  ext4   1G      # unencrypted (boot loader)
  PART  lvm    lvm    all     # LUKS-encrypted LVM PV
  LV    vg0    root   /       # root filesystem
  LV    vg0    swap   4G      # swap (also encrypted)
  ```

**Record the LUKS passphrase in the team password manager immediately.** Do not write it to disk anywhere.

### Milestone 0.3: Install Dropbear in initramfs

After first boot (in rescue mode or after login via LUKS unlock):

```bash
# Install Dropbear for remote LUKS unlock
apt install dropbear-initramfs

# Configure Dropbear port (use 2222, separate from main sshd on 22)
echo 'DROPBEAR_OPTIONS="-p 2222 -s"' >> /etc/dropbear/initramfs/dropbear.conf

# Add unlock SSH key to initramfs authorized_keys
mkdir -p /etc/dropbear/initramfs
echo "ssh-ed25519 AAAA... klai-gpu-unlock" > /etc/dropbear/initramfs/authorized_keys
chmod 600 /etc/dropbear/initramfs/authorized_keys

# Rebuild initramfs
update-initramfs -u

# Verify: check that dropbear binary is in initramfs
lsinitramfs /boot/initrd.img-$(uname -r) | grep dropbear
```

### Milestone 0.4: Validate Encrypted Boot

1. Reboot gpu-01
2. SSH into Dropbear on port 2222: `ssh -p 2222 root@5.9.10.215`
3. Run unlock command: `cryptroot-unlock` (enter LUKS passphrase)
4. Wait for full boot, then SSH normally: `ssh gpu-01` (configure in `~/.ssh/config`)
5. Verify: `lsblk` shows dm-crypt devices; `df -h` shows encrypted root mounted

### Technical Notes

- **Why Dropbear over auto-unlock**: Auto-unlock (TPM, Tang) requires network binding or TPM attestation. Dropbear is simpler, more transparent, and requires explicit operator action per reboot. For a server that rarely reboots, this is acceptable.
- **Boot partition**: `/boot` must remain unencrypted (GRUB needs to read it). Everything else is encrypted.
- **Dropbear key**: Use a separate keypair for initramfs unlock — not the same as the regular admin key. This key only exists to run `cryptroot-unlock`.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| LUKS passphrase lost | Low | Critical | Store in team password manager immediately; verify before completing Phase 0 |
| Dropbear misconfigured — no remote unlock possible | Medium | High | Test unlock from a different machine before declaring Phase 0 complete |
| Boot fails after encryption setup | Low | High | Keep rescue SSH session open during first reboot; Hetzner KVM console as fallback |

---

## Phase 1: Docker Setup (gpu-01)

**Priority: High — depends on Phase 0**

### Milestone 1.1: Base System Setup

```bash
ssh gpu-01

# Update system
apt update && apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
usermod -aG docker $USER

# Create working directory
mkdir -p /opt/klai-gpu
```

### Milestone 1.2: GPU Docker Compose

Create `/opt/klai-gpu/docker-compose.yml`:

```yaml
services:
  infinity:
    image: michaelf34/infinity:latest
    ports:
      - "127.0.0.1:7997:7997"
    environment:
      - INFINITY_MODEL_ID=BAAI/bge-m3
      - INFINITY_RERANKER_MODEL_ID=BAAI/bge-reranker-v2-m3
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]
    restart: unless-stopped

  bge-sparse:
    build:
      context: ./bge-m3-sparse
    ports:
      - "127.0.0.1:8001:8001"
    restart: unless-stopped

  whisper:
    image: ghcr.io/getklai/whisper-server:latest
    ports:
      - "127.0.0.1:8000:8000"
    environment:
      - WHISPER_MODEL=large-v3-turbo
      - DEVICE=cuda
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]
    restart: unless-stopped
```

**Note**: All `ports` bindings use `127.0.0.1:PORT:PORT` — never `PORT:PORT` (which binds to 0.0.0.0).

### Milestone 1.3: Service Validation on gpu-01

```bash
cd /opt/klai-gpu && docker compose up -d

# Check all services running
docker compose ps

# Verify localhost-only binding (SECURITY CHECK)
ss -tlnp | grep -E '7997|8001|8000'
# Expected: all lines show 127.0.0.1:PORT, NOT 0.0.0.0:PORT

# Test each service locally
curl -s http://127.0.0.1:7997/health     # Infinity
curl -s http://127.0.0.1:8001/health     # BGE-M3 sparse
curl -s http://127.0.0.1:8000/health     # faster-whisper

# Check GPU usage
nvidia-smi
```

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| GPU driver not installed | Medium | High | Install nvidia-driver + nvidia-docker2 before starting containers |
| VRAM insufficient for all three services | Low | High | Start services one by one, monitor nvidia-smi; adjust batch sizes if needed |
| bge-m3-sparse build context not on gpu-01 | Medium | Medium | Copy or clone the build context from core-01 first |

---

## Phase 2: SSH Tunnel Setup (core-01)

**Priority: High — depends on Phase 1**

### Milestone 2.1: Generate Dedicated SSH Keypair

```bash
# On core-01 — dedicated keypair for GPU tunnel only
ssh-keygen -t ed25519 -f /opt/klai/gpu-tunnel-key -N "" -C "klai-gpu-tunnel"

# Copy public key to gpu-01
ssh-copy-id -i /opt/klai/gpu-tunnel-key.pub gpu-01
# Or manually: cat /opt/klai/gpu-tunnel-key.pub | ssh gpu-01 "cat >> ~/.ssh/authorized_keys"

# Set correct permissions
chmod 600 /opt/klai/gpu-tunnel-key
chmod 644 /opt/klai/gpu-tunnel-key.pub
```

### Milestone 2.2: Create Systemd Tunnel Service

Create `/etc/systemd/system/gpu-tunnel.service` on core-01:

```ini
[Unit]
Description=GPU Inference SSH Tunnels (gpu-01)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/autossh -M 0 \
  -o "ServerAliveInterval 30" \
  -o "ServerAliveCountMax 3" \
  -o "ExitOnForwardFailure yes" \
  -o "StrictHostKeyChecking accept-new" \
  -N \
  -L 127.0.0.1:7997:127.0.0.1:7997 \
  -L 127.0.0.1:8001:127.0.0.1:8001 \
  -L 127.0.0.1:8000:127.0.0.1:8000 \
  -i /opt/klai/gpu-tunnel-key \
  root@5.9.10.215
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start
systemctl daemon-reload
systemctl enable --now gpu-tunnel

# Verify tunnels are up
systemctl status gpu-tunnel
curl -s http://localhost:7997/health
curl -s http://localhost:8001/health
curl -s http://localhost:8000/health
```

### Milestone 2.3: Test Auto-Reconnection

```bash
# Kill the SSH process
kill $(pgrep -f 'ssh.*gpu-tunnel-key')

# Wait and verify autossh reconnects
sleep 70
curl -s http://localhost:7997/health   # Should succeed
```

### Technical Notes

- **Local forward binds to 127.0.0.1 on both ends**: `-L 127.0.0.1:PORT:127.0.0.1:PORT` — the tunnel is only accessible from localhost on core-01, not from other containers on `klai-net`.
- **Implication**: consumer services (retrieval-api, knowledge-ingest, scribe-api) in Docker containers cannot reach `localhost` directly. Use `network_mode: host` OR `extra_hosts: ["host.docker.internal:host-gateway"]` for each consumer service.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Docker containers cannot reach localhost:PORT | High | High | Add `extra_hosts: ["host.docker.internal:host-gateway"]` to consumer services; update URLs to use `host.docker.internal` |
| Port conflict (7997/8001/8000 in use on core-01) | Medium | High | Stop old GPU services first; verify with `lsof -i :7997` |

---

## Phase 3: Core-01 Consumer Migration

**Priority: High — depends on Phase 2**

### Milestone 3.1: Stop Old GPU Services on core-01

```bash
cd /opt/klai

# Stop GPU containers
docker compose stop tei bge-sparse whisper

# Verify ports are free
lsof -nP -iTCP:8080 -sTCP:LISTEN   # TEI port
lsof -nP -iTCP:8001 -sTCP:LISTEN   # sparse port
lsof -nP -iTCP:8000 -sTCP:LISTEN   # whisper port
# All should return empty (or show tunnel processes, not old containers)
```

### Milestone 3.2: Update docker-compose.yml

In `deploy/docker-compose.yml`:

1. Comment out or profile-gate old GPU services (TEI, bge-sparse, whisper on core-01)
2. Add `extra_hosts` or check networking for consumer services
3. Update consumer environment variables:

```yaml
# retrieval-api
environment:
  - TEI_URL=http://host.docker.internal:7997        # was http://tei:8080
  - SPARSE_URL=http://host.docker.internal:8001      # was http://bge-sparse:8001
  - RERANKER_URL=http://host.docker.internal:7997    # was http://tei:8080 or separate

# knowledge-ingest
environment:
  - TEI_URL=http://host.docker.internal:7997
  - SPARSE_URL=http://host.docker.internal:8001

# scribe-api
environment:
  - WHISPER_URL=http://host.docker.internal:8000    # was http://whisper:8000
```

Also add to each consumer service:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

### Milestone 3.3: Restart Consumers and Validate

```bash
docker compose restart retrieval-api knowledge-ingest scribe-api

# Check logs for successful connections
docker logs --tail 20 klai-core-retrieval-api-1
docker logs --tail 20 klai-core-knowledge-ingest-1
docker logs --tail 20 klai-core-scribe-api-1

# End-to-end test: ingest a test document
# End-to-end test: run a search query
# End-to-end test: transcribe a test audio file
```

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Infinity API different from TEI | Medium | High | Test embedding API format; Infinity uses OpenAI-compatible `/v1/embeddings` |
| `host.docker.internal` not resolving | Low | Medium | Add `extra_hosts: ["host.docker.internal:host-gateway"]` to compose |
| SOPS env update needed | Medium | Medium | Update `core-01/.env.sops` if URLs are in SOPS; follow sops-env-sync pattern |

---

## Phase 4: Monitoring and Rollback

**Priority: Medium — completes the migration**

### Milestone 4.1: Health Check Script

Create `/opt/klai/scripts/gpu-health.sh`:

```bash
#!/bin/bash
set -euo pipefail

PUSH_URL="https://uptime.getklai.com/api/push/GPU_PUSH_TOKEN"
SERVICES=("7997:infinity" "8001:bge-sparse" "8000:whisper")

for service in "${SERVICES[@]}"; do
  port="${service%%:*}"
  name="${service##*:}"
  if ! curl --connect-timeout 2 --max-time 3 -sf "http://localhost:${port}/health" > /dev/null; then
    curl -sf "${PUSH_URL}?status=down&msg=${name}+unreachable" > /dev/null
    exit 1
  fi
done

curl -sf "${PUSH_URL}?status=up&msg=all+services+healthy" > /dev/null
```

Add cron entry on core-01:
```
* * * * * /opt/klai/scripts/gpu-health.sh >> /var/log/gpu-health.log 2>&1
```

### Milestone 4.2: Uptime Kuma Monitor

Follow `.claude/rules/klai/patterns/devops.md#uptime-kuma-add-monitor`:
- Type: Push
- Name: "GPU Tunnel Health"
- Token: GPU_PUSH_TOKEN (generate in Uptime Kuma)
- Alert threshold: 2 missed heartbeats

### Milestone 4.3: Rollback Procedure

Document and test the following rollback procedure:

1. Stop GPU tunnel: `systemctl stop gpu-tunnel`
2. Uncomment old GPU services in `deploy/docker-compose.yml`
3. Revert consumer env vars to Docker service names
4. Start old GPU services: `docker compose up -d tei bge-sparse whisper`
5. Restart consumers: `docker compose restart retrieval-api knowledge-ingest scribe-api`
6. Verify: `curl http://localhost:7997/health` fails (tunnel down), but consumers work via Docker names
7. Run end-to-end tests

Target: complete within 15 minutes. Practice at least once.

---

## Dependencies

```
Phase 0 (LUKS OS install)
    │
    └── Phase 1 (Docker setup on gpu-01)
            │
            └── Phase 2 (SSH Tunnels on core-01)
                    │
                    └── Phase 3 (Consumer migration)
                            │
                            └── Phase 4 (Monitoring + rollback drill)

Phase 4.3 (Rollback docs) — can be written in parallel with Phase 2
```

---

## Architecture Decisions

### AD-001: Dedicated Hetzner Server over Vast.ai

**Decision**: Use gpu-01 (Hetzner GEX44) instead of Vast.ai marketplace.
**Rationale**: Known host operator, physical hardware under Klai control, static IP, full OS control, Docker Compose available, customer data allowed.
**Trade-off**: Higher fixed cost vs. Vast.ai pay-per-use; justified by security posture.

### AD-002: LUKS + Dropbear over Auto-Unlock

**Decision**: Manual LUKS unlock via Dropbear SSH, not TPM/Tang auto-unlock.
**Rationale**: Simpler setup, no additional trust dependencies, explicit operator action per reboot ensures awareness.
**Trade-off**: Requires manual intervention after unplanned reboots; acceptable for a GPU inference server that rarely reboots.

### AD-003: SSH Tunnels over WireGuard

**Decision**: SSH tunnels instead of WireGuard VPN.
**Rationale**: Simpler to set up and maintain. WireGuard is viable on Hetzner (full kernel access) but adds operational complexity for a two-server setup. SSH tunnels are self-contained and use existing SSH keypair infrastructure.
**Trade-off**: Slightly higher per-connection overhead vs. WireGuard; acceptable for inference workloads.

### AD-004: Infinity Replaces TEI

**Decision**: Infinity handles both dense embeddings (bge-m3) and reranking (bge-reranker-v2-m3) in one container.
**Rationale**: Reduces container count from 4 to 3, simplifies VRAM budgeting, single health endpoint.
**Trade-off**: Single failure point for two capabilities — mitigated by Docker `restart: unless-stopped`.

### AD-005: localhost Tunnel Binding on Both Ends

**Decision**: SSH tunnel binds to `127.0.0.1` on both core-01 and gpu-01.
**Rationale**: Zero public exposure. Consumer services access via `host.docker.internal` from Docker bridge network.
**Trade-off**: Requires `extra_hosts` or `network_mode: host` for Docker consumers.
