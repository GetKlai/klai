---
id: SPEC-GPU-001
version: "1.0.0"
status: draft
created: "2026-03-27"
updated: "2026-03-27"
author: MoAI
priority: high
---

# SPEC-GPU-001: GPU Inference Service Migration to Vast.ai

## HISTORY

| Version | Date       | Author | Change                 |
|---------|------------|--------|------------------------|
| 1.0.0   | 2026-03-27 | MoAI   | Initial SPEC document  |

---

## Summary

Migrate three GPU inference services (Infinity embeddings/reranking, BGE-M3 sparse embeddings, faster-whisper STT) from core-01 Docker Compose to an external Vast.ai GPU instance (RTX 3090, 24GB VRAM, Belgium). All network traffic is secured via SSH tunnels with localhost-only binding. This is a test/staging setup -- no customer data flows through the Vast.ai instance. Production will use dedicated Hetzner GPU hardware.

---

## Environment

- **core-01**: Primary server running all Klai services via Docker Compose
- **GPU services currently on core-01**:
  - TEI (text-embeddings-inference) on port 8080 -- REPLACED by Infinity
  - BGE-M3 sparse sidecar on port 8001
  - faster-whisper on port 8000
- **Target GPU box**: Vast.ai instance, RTX 3090 (24GB VRAM), Belgium datacenter
- **Vast.ai constraints**:
  - Instances ARE Docker containers (no Docker-in-Docker)
  - Use supervisord for multi-process orchestration
  - onstart scripts limited to ~4000 characters
  - Port mapping uses random external ports (retrievable via API)
  - No kernel modules (WireGuard not available)
- **Consumer services on core-01**:
  - `retrieval-api` -- calls Infinity (embeddings + reranker) and BGE-M3 sparse
  - `knowledge-ingest` -- calls Infinity (embeddings) and BGE-M3 sparse
  - `scribe-api` -- calls faster-whisper
- **VRAM budget**: Infinity ~5GB + BGE-M3 sparse ~2GB + faster-whisper ~3GB = ~10GB of 24GB

---

## Assumptions

- **ASM-001**: The Vast.ai RTX 3090 instance provides at least 24GB VRAM with CUDA support
- **ASM-002**: SSH access to the Vast.ai instance is available with public key authentication
- **ASM-003**: Network latency between core-01 and the Belgium Vast.ai instance is acceptable (<20ms RTT for SSH tunnel overhead)
- **ASM-004**: Vast.ai instances persist across reboots (or can be recreated from the same onstart script)
- **ASM-005**: The Infinity server can serve both bge-m3 dense embeddings AND bge-reranker-v2-m3 reranking from a single process
- **ASM-006**: No customer data is processed on the Vast.ai instance -- test/staging data only
- **ASM-007**: autossh is available or installable on core-01
- **ASM-008**: The existing consumer services (retrieval-api, knowledge-ingest, scribe-api) accept URL configuration via environment variables

---

## Requirements

### Module 1: GPU Box Setup (Vast.ai)

**REQ-GPU-001** (Ubiquitous):
The GPU box SHALL run three inference services simultaneously: Infinity (bge-m3 + bge-reranker-v2-m3), BGE-M3 sparse sidecar, and faster-whisper.

**REQ-GPU-002** (Ubiquitous -- SECURITY CRITICAL):
All inference services on the GPU box SHALL bind exclusively to 127.0.0.1 -- never to 0.0.0.0.

**REQ-GPU-003** (Event-Driven):
WHEN the Vast.ai instance starts, THEN the onstart script SHALL install supervisord and launch all three inference services with health checks, completing within ~4000 characters.

**REQ-GPU-004** (State-Driven):
IF any inference service crashes, THEN supervisord SHALL automatically restart the failed service within 30 seconds.

**REQ-GPU-005** (Unwanted):
The GPU box SHALL NOT expose any inference service ports to the public network.

**REQ-GPU-006** (State-Driven):
IF total VRAM usage exceeds 20GB (of 24GB available), THEN the system SHALL log a warning and the operator SHALL be alerted.

### Module 2: SSH Tunnel Security

**REQ-SSH-001** (Ubiquitous -- SECURITY CRITICAL):
All communication between core-01 and the GPU box SHALL be encrypted via SSH tunnels.

**REQ-SSH-002** (Ubiquitous):
A dedicated SSH keypair SHALL be generated for GPU tunnel authentication -- personal keys SHALL NOT be reused.

**REQ-SSH-003** (Event-Driven):
WHEN an SSH tunnel drops, THEN autossh SHALL automatically re-establish the connection within 60 seconds.

**REQ-SSH-004** (Ubiquitous):
Three SSH tunnels SHALL be maintained persistently:
- localhost:7997 on core-01 forward to 127.0.0.1:7997 on GPU box (Infinity)
- localhost:8001 on core-01 forward to 127.0.0.1:8001 on GPU box (BGE-M3 sparse)
- localhost:8000 on core-01 forward to 127.0.0.1:8000 on GPU box (faster-whisper)

**REQ-SSH-005** (Unwanted):
The SSH tunnel configuration SHALL NOT use password authentication -- only public key authentication is permitted.

**REQ-SSH-006** (Event-Driven):
WHEN the GPU box IP address changes (Vast.ai re-provision), THEN the SSH tunnel configuration SHALL be updatable without restarting consumer services.

### Module 3: Core-01 Consumer Migration

**REQ-MIG-001** (Event-Driven):
WHEN consumer services are restarted with updated environment variables, THEN they SHALL connect to inference services via localhost SSH tunnel ports instead of Docker service names.

**REQ-MIG-002** (Ubiquitous):
The following environment variable mappings SHALL be applied:
- `TEI_URL` changed to `http://localhost:7997` (was Docker service name)
- `SPARSE_URL` changed to `http://localhost:8001` (was Docker service name)
- `WHISPER_URL` changed to `http://localhost:8000` (was Docker service name)
- `RERANKER_URL` changed to `http://localhost:7997` (same Infinity instance as TEI_URL)

**REQ-MIG-003** (Event-Driven):
WHEN GPU services are migrated off core-01, THEN the old GPU service containers (TEI, BGE-M3 sparse, faster-whisper) SHALL be stopped and disabled in docker-compose.yml.

**REQ-MIG-004** (Unwanted):
The migration SHALL NOT require code changes to consumer services -- only environment variable updates.

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

**REQ-SEC-001** (Ubiquitous -- SECURITY CRITICAL):
No customer data SHALL be processed on the Vast.ai instance -- only test/staging data is permitted.

**REQ-SEC-002** (Ubiquitous):
The RTX 3090 does NOT support NVIDIA Confidential Computing -- this is accepted for test data only.

**REQ-SEC-003** (Unwanted):
SSH private keys for GPU tunnel authentication SHALL NOT be stored in version control or shared configuration files.

---

## Specifications

### Service Architecture

```
                        SSH Tunnels (encrypted)
core-01                 ───────────────────────>  Vast.ai GPU Box (RTX 3090)
┌─────────────────┐                               ┌──────────────────────────┐
│ retrieval-api   │──localhost:7997──tunnel──>     │ Infinity (bge-m3 +       │
│                 │──localhost:8001──tunnel──>     │   bge-reranker-v2-m3)    │
│ knowledge-ingest│──localhost:7997──tunnel──>     │ BGE-M3 sparse sidecar    │
│                 │──localhost:8001──tunnel──>     │ faster-whisper           │
│ scribe-api      │──localhost:8000──tunnel──>     │                          │
├─────────────────┤                               │ supervisord orchestrator │
│ autossh (3      │                               │ All bound to 127.0.0.1   │
│   tunnels)      │                               └──────────────────────────┘
└─────────────────┘
```

### Port Mapping

| Service | GPU Box Port | Core-01 Tunnel Port | Consumers |
|---------|-------------|--------------------|-----------|
| Infinity (embeddings + reranker) | 127.0.0.1:7997 | localhost:7997 | retrieval-api, knowledge-ingest |
| BGE-M3 sparse | 127.0.0.1:8001 | localhost:8001 | retrieval-api, knowledge-ingest |
| faster-whisper | 127.0.0.1:8000 | localhost:8000 | scribe-api |

### Files Changed on Core-01

| File | Change |
|------|--------|
| `deploy/docker-compose.yml` | Disable/remove GPU service definitions; update consumer env vars |
| `deploy/.env` or environment sections | Update TEI_URL, SPARSE_URL, WHISPER_URL, RERANKER_URL |
| New: SSH tunnel systemd unit or supervisor config | autossh configuration for 3 tunnels |
| New: Health check script | Tunnel connectivity monitoring |

### Production Path

This Vast.ai setup is explicitly test/staging only. The production migration path:
1. Procure dedicated Hetzner GPU server (EU datacenter)
2. Replicate the same supervisord + services setup
3. Replace Vast.ai SSH tunnel endpoints with Hetzner endpoints
4. Enable customer data processing only on dedicated hardware
5. Consider WireGuard VPN (available on dedicated hardware) as SSH tunnel replacement

---

## Traceability

| Requirement | Plan Reference | Acceptance Reference |
|-------------|---------------|---------------------|
| REQ-GPU-001..006 | Phase 1: GPU Box Setup | AC-GPU-001..006 |
| REQ-SSH-001..006 | Phase 2: SSH Tunnel Setup | AC-SSH-001..006 |
| REQ-MIG-001..004 | Phase 3: Core-01 Migration | AC-MIG-001..004 |
| REQ-MON-001..004 | Phase 4: Monitoring & Rollback | AC-MON-001..004 |
| REQ-SEC-001..003 | All Phases (cross-cutting) | AC-SEC-001..003 |
