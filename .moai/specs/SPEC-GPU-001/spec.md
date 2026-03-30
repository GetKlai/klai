---
id: SPEC-GPU-001
version: "2.0.0"
status: draft
created: "2026-03-27"
updated: "2026-03-29"
author: MoAI
priority: high
---

# SPEC-GPU-001: GPU Inference Service Migration to gpu-01 (Hetzner)

## HISTORY

| Version | Date       | Author | Change                                        |
|---------|------------|--------|-----------------------------------------------|
| 1.0.0   | 2026-03-27 | MoAI   | Initial SPEC — Vast.ai target                 |
| 2.0.0   | 2026-03-29 | MoAI   | Rewrite — Hetzner dedicated server (gpu-01)   |

---

## Summary

Migrate three GPU inference services (Infinity embeddings/reranking, BGE-M3 sparse embeddings, faster-whisper STT) from core-01 Docker Compose to a dedicated Hetzner server **gpu-01** (GEX44, IP: 5.9.10.215, FSN1-DC13). Services run as Docker Compose containers on gpu-01. Network communication between core-01 and gpu-01 is secured via SSH tunnels. Both servers are in Hetzner datacenters and Klai controls the hardware.

---

## Environment

- **core-01**: Primary server running all Klai services via Docker Compose (Hetzner, EU)
- **gpu-01**: Dedicated Hetzner server `GEX44 #2963286`, IP `5.9.10.215`, FSN1-DC13 (Germany)
- **Naming convention**: `gpu-01` — follows `core-01` naming pattern
- **GPU services currently on core-01 (to be migrated)**:
  - TEI (text-embeddings-inference) on port 8080 — REPLACED by Infinity
  - BGE-M3 sparse sidecar on port 8001
  - faster-whisper on port 8000
- **Consumer services on core-01 (unchanged)**:
  - `retrieval-api` — calls Infinity (embeddings + reranker) and BGE-M3 sparse
  - `knowledge-ingest` — calls Infinity (embeddings) and BGE-M3 sparse
  - `scribe-api` — calls faster-whisper
- **Port layout**:
  - Infinity (bge-m3 + bge-reranker-v2-m3): `127.0.0.1:7997` on gpu-01
  - BGE-M3 sparse: `127.0.0.1:8001` on gpu-01
  - faster-whisper: `127.0.0.1:8000` on gpu-01

---

## Assumptions

- **ASM-001**: gpu-01 has GPU hardware with CUDA support and sufficient VRAM for all three services (~10GB minimum)
- **ASM-002**: Docker is installed (or installable) on gpu-01
- **ASM-003**: SSH access to gpu-01 is available from core-01 using the `core-01` SSH config alias or direct `5.9.10.215`
- **ASM-004**: IP 5.9.10.215 is stable (dedicated Hetzner server — not a marketplace VM)
- **ASM-005**: Infinity can serve both bge-m3 dense embeddings AND bge-reranker-v2-m3 reranking from a single process
- **ASM-006**: Consumer services accept URL configuration via environment variables
- **ASM-007**: autossh is available or installable on core-01

---

## Requirements

### Module 1: GPU Box Setup (gpu-01)

**REQ-GPU-001** (Ubiquitous):
The gpu-01 server SHALL run three inference services as Docker containers: Infinity (bge-m3 + bge-reranker-v2-m3), BGE-M3 sparse sidecar, and faster-whisper.

**REQ-GPU-002** (Ubiquitous — SECURITY CRITICAL):
All inference service containers on gpu-01 SHALL bind exclusively to `127.0.0.1` — never to `0.0.0.0`.

**REQ-GPU-003** (Ubiquitous):
Services on gpu-01 SHALL be defined as a Docker Compose file (`/opt/klai-gpu/docker-compose.yml`) using the same images and patterns as core-01.

**REQ-GPU-004** (State-Driven):
IF any inference service container crashes, THEN Docker SHALL automatically restart it (`restart: unless-stopped`).

**REQ-GPU-005** (Unwanted):
The gpu-01 server SHALL NOT expose inference service ports (7997, 8001, 8000) to the public network.

**REQ-GPU-006** (State-Driven):
IF total VRAM usage exceeds 80% of available VRAM, THEN the system SHALL log a warning visible in container logs.

### Module 2: SSH Tunnel Security

**REQ-SSH-001** (Ubiquitous — SECURITY CRITICAL):
All communication between core-01 and gpu-01 SHALL be encrypted via SSH tunnels.

**REQ-SSH-002** (Ubiquitous):
A dedicated SSH keypair SHALL be generated for GPU tunnel authentication — personal keys or existing service keys SHALL NOT be reused.

**REQ-SSH-003** (Event-Driven):
WHEN an SSH tunnel drops, THEN autossh SHALL automatically re-establish the connection within 60 seconds.

**REQ-SSH-004** (Ubiquitous):
Three SSH local port forwards SHALL be maintained persistently via a single autossh process:
- `localhost:7997` on core-01 → `127.0.0.1:7997` on gpu-01 (Infinity)
- `localhost:8001` on core-01 → `127.0.0.1:8001` on gpu-01 (BGE-M3 sparse)
- `localhost:8000` on core-01 → `127.0.0.1:8000` on gpu-01 (faster-whisper)

**REQ-SSH-005** (Unwanted):
The SSH tunnel configuration SHALL NOT use password authentication — only public key authentication is permitted.

**REQ-SSH-006** (Ubiquitous):
The autossh tunnel SHALL run as a systemd service on core-01 with `Restart=always`.

### Module 3: Core-01 Consumer Migration

**REQ-MIG-001** (Event-Driven):
WHEN consumer services are restarted with updated environment variables, THEN they SHALL connect to inference services via localhost SSH tunnel ports instead of Docker service names.

**REQ-MIG-002** (Ubiquitous):
The following environment variable mappings SHALL be applied:
- `TEI_URL` → `http://localhost:7997` (was Docker service name)
- `SPARSE_URL` → `http://localhost:8001` (was Docker service name)
- `WHISPER_URL` → `http://localhost:8000` (was Docker service name)
- `RERANKER_URL` → `http://localhost:7997` (same Infinity instance as TEI_URL)

**REQ-MIG-003** (Event-Driven):
WHEN GPU services are migrated to gpu-01, THEN the old GPU service containers (TEI, BGE-M3 sparse, faster-whisper) SHALL be stopped and disabled in `deploy/docker-compose.yml` on core-01.

**REQ-MIG-004** (Unwanted):
The migration SHALL NOT require code changes to consumer services — only environment variable updates.

### Module 4: Monitoring and Rollback

**REQ-MON-001** (Ubiquitous):
A health check script SHALL run on core-01 that verifies tunnel connectivity to all three inference services every 60 seconds.

**REQ-MON-002** (Event-Driven):
WHEN a health check fails for any inference service, THEN an Uptime Kuma push monitor SHALL be alerted.

**REQ-MON-003** (Ubiquitous):
A documented rollback procedure SHALL exist that can restore GPU services to core-01 within 15 minutes.

**REQ-MON-004** (Event-Driven):
WHEN rollback is triggered, THEN the old GPU service containers on core-01 SHALL be re-enabled and started, and consumer environment variables SHALL be reverted to Docker service names.

### Module 5: Data Security

**REQ-SEC-001** (Ubiquitous):
All inference service traffic between core-01 and gpu-01 SHALL be encrypted via SSH tunnel (both servers under Klai control in Hetzner EU datacenters).

**REQ-SEC-002** (Unwanted):
SSH private keys for GPU tunnel authentication SHALL NOT be stored in version control.

**REQ-SEC-003** (Ubiquitous):
Access to gpu-01 SHALL be restricted to authorized keys only — password authentication SHALL be disabled in sshd_config.

### Module 6: Full-Disk Encryption (gpu-01)

**REQ-ENC-001** (Ubiquitous — SECURITY CRITICAL):
The gpu-01 server SHALL use LUKS full-disk encryption, enabled during OS installation via Hetzner Installimage in rescue mode.

**REQ-ENC-002** (Ubiquitous):
An SSH server (Dropbear) SHALL be embedded in the initramfs so that the LUKS passphrase can be entered remotely after each reboot without physical access.

**REQ-ENC-003** (Ubiquitous):
The Dropbear SSH server in initramfs SHALL listen on a different port than the main sshd (e.g., port 2222) and accept only the designated unlock keypair.

**REQ-ENC-004** (Ubiquitous):
The LUKS passphrase SHALL be stored in the team password manager — NOT in SOPS, NOT in any file on the server, NOT in git.

**REQ-ENC-005** (Event-Driven):
WHEN gpu-01 reboots (planned or unplanned), THEN the operator SHALL SSH into Dropbear to unlock the disk before the system becomes operational.

**REQ-ENC-006** (Unwanted):
The system SHALL NOT auto-unlock the disk without operator authentication — no TPM-based or network-bound auto-unlock unless explicitly reviewed.

---

## Specifications

### Service Architecture

```
core-01 (Hetzner DE)            SSH Tunnels (encrypted)           gpu-01 (Hetzner DE, 5.9.10.215)
┌────────────────────┐          ─────────────────────────>        ┌───────────────────────────────┐
│ retrieval-api      │──localhost:7997──>                          │ Infinity :7997                │
│ knowledge-ingest   │──localhost:7997──>                          │   (bge-m3 + bge-reranker)     │
│                    │──localhost:8001──>                          │ BGE-M3 sparse :8001           │
│ scribe-api         │──localhost:8000──>                          │ faster-whisper :8000          │
├────────────────────┤                                             │                               │
│ autossh            │                                             │ All bound to 127.0.0.1        │
│ (systemd service)  │                                             │ Docker Compose managed        │
└────────────────────┘                                             └───────────────────────────────┘
```

### Port Mapping

| Service | gpu-01 Port | core-01 Tunnel Port | Consumers |
|---------|------------|---------------------|-----------|
| Infinity (embeddings + reranker) | 127.0.0.1:7997 | localhost:7997 | retrieval-api, knowledge-ingest |
| BGE-M3 sparse | 127.0.0.1:8001 | localhost:8001 | retrieval-api, knowledge-ingest |
| faster-whisper | 127.0.0.1:8000 | localhost:8000 | scribe-api |

### Files Changed

| Server | File | Change |
|--------|------|--------|
| gpu-01 | `/opt/klai-gpu/docker-compose.yml` (new) | GPU services in Docker Compose |
| core-01 | `deploy/docker-compose.yml` | Disable old GPU services; update consumer env vars |
| core-01 | `/opt/klai/.env.sops` | Update TEI_URL, SPARSE_URL, WHISPER_URL, RERANKER_URL |
| core-01 | `/etc/systemd/system/gpu-tunnel.service` (new) | autossh tunnel systemd unit |
| core-01 | `/opt/klai/scripts/gpu-health.sh` (new) | Health check script |

### Security Posture

Both servers (core-01 and gpu-01) are in Hetzner EU datacenters under Klai's exclusive control. The threat model is fundamentally different from a marketplace (Vast.ai):

| Risk | Status |
|------|--------|
| Unknown host operator | **Eliminated** — Hetzner is the datacenter, Klai owns the hardware |
| Data in transit (internet) | **Mitigated** — SSH tunnel encryption |
| Customer data on shared hardware | **Acceptable** — dedicated server, Klai controls all access |
| Physical disk theft / datacenter access | **Mitigated** — LUKS full-disk encryption (REQ-ENC-001) |
| Data at rest without encryption | **Mitigated** — LUKS on all partitions |
| Unattended reboot disk unlock | **Accepted risk** — Dropbear enables remote unlock; auto-unlock explicitly disabled |
| NVIDIA Confidential Computing | Not required (no untrusted host operator) |

---

## Traceability

| Requirement | Plan Reference | Acceptance Reference |
|-------------|---------------|---------------------|
| REQ-GPU-001..006 | Phase 1: GPU Box Setup | AC-GPU-001..006 |
| REQ-SSH-001..006 | Phase 2: SSH Tunnel Setup | AC-SSH-001..006 |
| REQ-MIG-001..004 | Phase 3: Core-01 Migration | AC-MIG-001..004 |
| REQ-MON-001..004 | Phase 4: Monitoring & Rollback | AC-MON-001..004 |
| REQ-SEC-001..003 | All Phases (cross-cutting) | AC-SEC-001..003 |
| REQ-ENC-001..006 | Phase 0: OS Install + LUKS | AC-ENC-001..005 |
