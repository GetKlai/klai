---
id: SPEC-GPU-001
document: plan
version: "1.0.0"
status: draft
created: "2026-03-27"
updated: "2026-03-27"
---

# SPEC-GPU-001: Implementation Plan -- GPU Inference Service Migration

## Overview

Four-phase migration of GPU inference services from core-01 to a Vast.ai GPU box, secured by SSH tunnels with zero code changes to consumer services.

---

## Phase 1: GPU Box Setup (Vast.ai)

**Priority: High -- Primary Goal**

### Milestone 1.1: Vast.ai Instance Provisioning

- Create Vast.ai instance: RTX 3090, 24GB VRAM, Belgium datacenter
- Verify SSH access is enabled and accessible from core-01
- Confirm CUDA driver availability and GPU health

### Milestone 1.2: Onstart Script Development

Write an onstart.sh script (~4000 chars max) that:

1. Installs supervisord via pip (Vast.ai images typically have Python)
2. Writes supervisord.conf with three program sections:
   - **infinity**: Runs Infinity server with bge-m3 and bge-reranker-v2-m3 models, binding to 127.0.0.1:7997
   - **bge-sparse**: Runs BGE-M3 sparse sidecar, binding to 127.0.0.1:8001
   - **whisper**: Runs faster-whisper server, binding to 127.0.0.1:8000
3. Starts supervisord in foreground mode
4. All services configured with autorestart=true

### Milestone 1.3: Service Validation on GPU Box

- SSH into the instance and verify all three services are running
- Confirm each service binds to 127.0.0.1 only (verify with `ss -tlnp`)
- Run test inference calls locally on the GPU box:
  - Infinity: embedding request + reranking request
  - BGE-M3 sparse: sparse embedding request
  - faster-whisper: transcription request with test audio
- Confirm VRAM usage is within budget (~10GB of 24GB)

### Technical Approach

The onstart script must fit within Vast.ai's ~4000 char limit. Strategy:
- Inline the supervisord config as a heredoc
- Use pip to install supervisor (avoid apt for speed)
- Pull model weights at first boot (cached in persistent storage on subsequent boots)
- Keep the script minimal -- complex logic goes into the supervisord config

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Onstart script exceeds 4000 chars | Medium | High | Pre-install supervisor in custom template; minimize script to config only |
| Model download takes too long on first boot | Medium | Medium | Use Vast.ai persistent storage; pre-cache models in template |
| VRAM contention between services | Low | High | Monitor with nvidia-smi; reduce batch sizes if needed |

---

## Phase 2: SSH Tunnel Setup (core-01)

**Priority: High -- Primary Goal**

### Milestone 2.1: SSH Key Generation

- Generate a dedicated Ed25519 keypair on core-01: `ssh-keygen -t ed25519 -f /opt/klai/gpu-tunnel-key -N ""`
- Add the public key to the Vast.ai instance's `~/.ssh/authorized_keys`
- Verify SSH connectivity: `ssh -i /opt/klai/gpu-tunnel-key -p <vast-port> root@<vast-ip>`
- Store private key with restricted permissions (600) owned by root

### Milestone 2.2: autossh Configuration

Install autossh on core-01 (if not present) and create a systemd service:

```
# /etc/systemd/system/gpu-tunnel.service
[Unit]
Description=GPU Inference SSH Tunnels (Vast.ai)
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
  -L 7997:127.0.0.1:7997 \
  -L 8001:127.0.0.1:8001 \
  -L 8000:127.0.0.1:8000 \
  -i /opt/klai/gpu-tunnel-key \
  -p <VAST_SSH_PORT> \
  root@<VAST_IP>
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Milestone 2.3: Tunnel Validation

- Start the systemd service: `systemctl enable --now gpu-tunnel`
- Verify all three tunnels are up: `curl -s http://localhost:7997/health`, `curl -s http://localhost:8001/health`, `curl -s http://localhost:8000/health`
- Test tunnel recovery: kill the SSH process, verify autossh reconnects within 60 seconds

### Technical Approach

Single autossh process with three `-L` flags (one per tunnel) is simpler and more reliable than three separate autossh processes. The `-M 0` flag disables the autossh monitoring port and relies on SSH `ServerAliveInterval` for liveness detection, which is more robust.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Vast.ai IP changes on instance restart | Medium | High | Store IP in env file; update systemd unit; script to automate |
| SSH tunnel drops under load | Low | Medium | autossh auto-reconnects; ServerAliveInterval detects drops |
| Port conflict on core-01 (7997/8001/8000 already in use) | Medium | High | Stop old GPU services first (Phase 3); verify ports free before tunnel start |

---

## Phase 3: Core-01 Consumer Migration

**Priority: High -- Primary Goal**

### Milestone 3.1: Disable Old GPU Services

- Edit `deploy/docker-compose.yml`: comment out or add `profiles: ["gpu-local"]` to the old GPU service definitions (TEI, BGE-M3 sparse, faster-whisper)
- Stop old containers: `docker compose stop tei bge-sparse whisper` (use actual service names)
- Verify ports 7997, 8001, 8000 are free on core-01

### Milestone 3.2: Update Consumer Environment Variables

Update environment sections in `deploy/docker-compose.yml` or `deploy/.env`:

| Variable | Old Value | New Value |
|----------|----------|-----------|
| `TEI_URL` | `http://tei:8080` (or similar Docker name) | `http://localhost:7997` |
| `SPARSE_URL` | `http://bge-sparse:8001` (or similar) | `http://localhost:8001` |
| `WHISPER_URL` | `http://whisper:8000` (or similar) | `http://localhost:8000` |
| `RERANKER_URL` | `http://tei:8080` (or similar) | `http://localhost:7997` |

Note: `TEI_URL` and `RERANKER_URL` both point to the same Infinity instance (port 7997) since Infinity handles both dense embeddings and reranking.

### Milestone 3.3: Restart Consumer Services

- Restart consumers: `docker compose restart retrieval-api knowledge-ingest scribe-api`
- Verify each consumer can reach its inference service through the tunnel
- Run end-to-end test flows:
  - Upload a document to knowledge-ingest (triggers embedding + sparse)
  - Run a search query on retrieval-api (triggers embedding + reranking)
  - Upload an audio file to scribe-api (triggers whisper transcription)

### Technical Approach

Consumer services use `host.docker.internal` or Docker `network_mode: host` to reach localhost tunnels. Verify which networking mode the consumers use. If they use Docker bridge networking, `localhost` inside the container refers to the container itself -- may need `extra_hosts: ["host.docker.internal:host-gateway"]` or `network_mode: host`.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Docker container cannot reach localhost tunnels | High | High | Use `network_mode: host` or `extra_hosts` mapping |
| Consumer service expects different API format from Infinity vs TEI | Medium | High | Test Infinity API compatibility with TEI clients; adjust if needed |
| Rollback needed during migration | Low | Medium | Keep old service definitions commented (not deleted); quick re-enable |

---

## Phase 4: Monitoring and Rollback

**Priority: Medium -- Secondary Goal**

### Milestone 4.1: Health Check Script

Create `/opt/klai/scripts/gpu-health.sh`:
- Check tunnel connectivity to all three services via HTTP health endpoints
- Report status to Uptime Kuma via push token
- Run via cron every 60 seconds

### Milestone 4.2: Uptime Kuma Integration

- Add push monitor for GPU tunnel health (follow `claude-docs/patterns/devops.md#uptime-kuma-add-monitor`)
- Add to status page if customer-facing (probably not for test/staging)
- Integrate into `push-health.sh` on core-01

### Milestone 4.3: Rollback Procedure Documentation

Document the rollback procedure:

1. Stop the GPU tunnel service: `systemctl stop gpu-tunnel`
2. Uncomment old GPU services in `deploy/docker-compose.yml`
3. Revert consumer environment variables to Docker service names
4. Start old GPU services: `docker compose up -d tei bge-sparse whisper`
5. Restart consumer services: `docker compose restart retrieval-api knowledge-ingest scribe-api`
6. Verify end-to-end functionality

Target: rollback completes within 15 minutes.

### Milestone 4.4: Rollback Drill

- Practice the rollback procedure at least once before declaring migration complete
- Time the procedure to verify it meets the 15-minute target
- Document any issues encountered during the drill

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Health check false positives | Low | Medium | Test health check script thoroughly; use conservative thresholds |
| Rollback takes longer than 15 minutes | Low | High | Practice rollback drill; pre-stage old config as commented blocks |

---

## Dependencies

```
Phase 1 (GPU Box Setup)
    │
    ├── Phase 2 (SSH Tunnels) ── depends on Phase 1 (needs running GPU box)
    │       │
    │       └── Phase 3 (Consumer Migration) ── depends on Phase 2 (needs working tunnels)
    │               │
    │               └── Phase 4 (Monitoring) ── depends on Phase 3 (needs full system running)
    │
    └── Phase 4.3 (Rollback Docs) ── can start in parallel with Phase 2
```

---

## Architecture Decisions

### AD-001: SSH Tunnels over WireGuard VPN

**Decision**: Use SSH tunnels instead of WireGuard VPN.
**Rationale**: Vast.ai instances are Docker containers without kernel module support. WireGuard requires kernel modules. SSH tunnels work in any environment and need no special privileges.
**Trade-off**: Slightly higher overhead per connection vs. WireGuard, but acceptable for three services.

### AD-002: Single autossh Process

**Decision**: Use one autossh process with three `-L` forward flags.
**Rationale**: Simpler to manage than three separate processes. Single point of monitoring. All tunnels reconnect together.
**Trade-off**: If one tunnel fails, all tunnels restart -- but this is acceptable since partial connectivity is not useful.

### AD-003: Infinity Replaces TEI

**Decision**: Replace TEI with Infinity for both dense embeddings and reranking.
**Rationale**: Infinity handles both tasks in a single process, reducing VRAM usage and operational complexity. TEI served only embeddings; reranking was a separate service.
**Trade-off**: Single point of failure for two capabilities -- mitigated by supervisord autorestart and monitoring.

### AD-004: Test/Staging Only -- No Customer Data

**Decision**: Vast.ai is explicitly test/staging only; no customer data permitted.
**Rationale**: Vast.ai is a marketplace with unknown host operators. RTX 3090 lacks hardware confidential computing. The security posture is acceptable only for test data.
**Trade-off**: Must maintain separate infrastructure for production (Hetzner dedicated GPU).

---

## Production Migration Path (Future)

When moving from Vast.ai to dedicated Hetzner GPU hardware:

1. Same supervisord + service setup (onstart script becomes a deployment script)
2. SSH tunnel endpoints updated to Hetzner IP (or replaced with WireGuard VPN)
3. Customer data processing enabled
4. Enhanced monitoring with GPU metrics (temperature, utilization, VRAM)
5. Consider redundancy (second GPU box as failover)
