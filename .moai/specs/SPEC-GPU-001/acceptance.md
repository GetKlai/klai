---
id: SPEC-GPU-001
document: acceptance
version: "1.0.0"
status: draft
created: "2026-03-27"
updated: "2026-03-27"
---

# SPEC-GPU-001: Acceptance Criteria -- GPU Inference Service Migration

## Definition of Done

All of the following must be true before SPEC-GPU-001 is considered complete:

- [ ] All three inference services running on Vast.ai GPU box
- [ ] All services bound to 127.0.0.1 only (verified)
- [ ] SSH tunnels operational and auto-reconnecting
- [ ] Consumer services on core-01 using tunnels successfully
- [ ] End-to-end flows verified (embed, rerank, sparse, transcribe)
- [ ] Health monitoring active with Uptime Kuma alerts
- [ ] Rollback procedure documented and tested
- [ ] No customer data on Vast.ai instance
- [ ] Old GPU services disabled on core-01

---

## Module 1: GPU Box Setup

### AC-GPU-001: All Three Services Running

**Given** the Vast.ai instance has started and the onstart script has executed
**When** I SSH into the GPU box and run `supervisorctl status`
**Then** I see three services (infinity, bge-sparse, whisper) all in RUNNING state

### AC-GPU-002: Localhost-Only Binding (SECURITY CRITICAL)

**Given** all three inference services are running on the GPU box
**When** I run `ss -tlnp` on the GPU box
**Then** all listening ports (7997, 8001, 8000) show bind address `127.0.0.1` and NOT `0.0.0.0` or `*`

### AC-GPU-003: Onstart Script Size Constraint

**Given** the onstart.sh script has been written
**When** I check the character count with `wc -c onstart.sh`
**Then** the count is at most 4000 characters

### AC-GPU-004: Supervisord Auto-Restart

**Given** all three services are running under supervisord
**When** I kill one service process (e.g., `kill <infinity-pid>`)
**Then** supervisord restarts the service within 30 seconds
**And** `supervisorctl status` shows the service back in RUNNING state

### AC-GPU-005: No Public Port Exposure

**Given** the GPU box has a public IP
**When** I run a port scan from an external host against the GPU box IP on ports 7997, 8001, 8000
**Then** all three ports are closed/filtered (no response or connection refused)

### AC-GPU-006: VRAM Usage Within Budget

**Given** all three services are loaded and idle
**When** I run `nvidia-smi` on the GPU box
**Then** total VRAM usage is below 20GB (of 24GB available)

### AC-GPU-007: Infinity Serves Both Embeddings and Reranking

**Given** the Infinity service is running on the GPU box
**When** I send an embedding request to `http://127.0.0.1:7997/embeddings` with model bge-m3
**Then** I receive a valid embedding vector response
**And** when I send a reranking request to `http://127.0.0.1:7997/rerank` with model bge-reranker-v2-m3
**Then** I receive a valid reranking score response

### AC-GPU-008: BGE-M3 Sparse Sidecar Operational

**Given** the BGE-M3 sparse service is running on the GPU box
**When** I send a sparse embedding request to `http://127.0.0.1:8001`
**Then** I receive a valid sparse embedding response with token weights

### AC-GPU-009: Faster-Whisper Operational

**Given** the faster-whisper service is running on the GPU box
**When** I send a test audio file for transcription to `http://127.0.0.1:8000`
**Then** I receive a valid transcription text response

---

## Module 2: SSH Tunnel Security

### AC-SSH-001: Encrypted Communication

**Given** the SSH tunnel service is running on core-01
**When** I inspect the autossh process with `ps aux | grep autossh`
**Then** the process shows SSH connection with `-L` local forward flags and no plaintext proxy

### AC-SSH-002: Dedicated SSH Keypair

**Given** the GPU tunnel SSH key exists at `/opt/klai/gpu-tunnel-key`
**When** I check the key
**Then** it is an Ed25519 key (not reusing any existing personal or service key)
**And** the private key has permissions `600`
**And** the key is not present in any git repository

### AC-SSH-003: Auto-Reconnection

**Given** the SSH tunnel is active and healthy
**When** I kill the SSH process (`kill $(pgrep -f 'ssh.*gpu-tunnel'`)
**Then** autossh re-establishes all three tunnels within 60 seconds
**And** `curl -s http://localhost:7997/health` returns a successful response

### AC-SSH-004: All Three Tunnels Operational

**Given** the gpu-tunnel systemd service is running
**When** I test connectivity to each tunnel endpoint
**Then** `curl -s http://localhost:7997/health` succeeds (Infinity)
**And** `curl -s http://localhost:8001/health` succeeds (BGE-M3 sparse)
**And** `curl -s http://localhost:8000/health` succeeds (faster-whisper)

### AC-SSH-005: No Password Authentication

**Given** the SSH tunnel configuration in the systemd unit
**When** I inspect the ExecStart command
**Then** it uses `-i /opt/klai/gpu-tunnel-key` for key-based auth
**And** does NOT contain any password or `-o PasswordAuthentication=yes`

### AC-SSH-006: IP Update Without Consumer Restart

**Given** the Vast.ai instance IP has changed
**When** I update the IP in the systemd unit and restart the tunnel service
**Then** consumer services (retrieval-api, knowledge-ingest, scribe-api) continue to work without restart
**And** they connect to the new GPU box through the same localhost ports

---

## Module 3: Core-01 Consumer Migration

### AC-MIG-001: Consumer Services Use Tunnel Ports

**Given** consumer environment variables have been updated to localhost tunnel ports
**When** I restart consumer services and check their logs
**Then** retrieval-api connects to `http://localhost:7997` for embeddings/reranking and `http://localhost:8001` for sparse
**And** knowledge-ingest connects to `http://localhost:7997` for embeddings and `http://localhost:8001` for sparse
**And** scribe-api connects to `http://localhost:8000` for transcription

### AC-MIG-002: Correct Environment Variable Values

**Given** the docker-compose.yml or .env file has been updated
**When** I inspect the running consumer containers' environment
**Then** `TEI_URL` equals `http://localhost:7997`
**And** `SPARSE_URL` equals `http://localhost:8001`
**And** `WHISPER_URL` equals `http://localhost:8000`
**And** `RERANKER_URL` equals `http://localhost:7997`

### AC-MIG-003: Old GPU Services Disabled

**Given** the migration to Vast.ai GPU is complete
**When** I run `docker compose ps` on core-01
**Then** the old GPU service containers (TEI, BGE-M3 sparse, faster-whisper) are NOT running
**And** the docker-compose.yml has them commented out or moved to a non-default profile

### AC-MIG-004: No Code Changes Required

**Given** the migration involves only environment variable changes
**When** I compare the consumer service source code before and after migration
**Then** zero lines of application code have changed
**And** only configuration/environment files were modified

---

## Module 4: Monitoring and Rollback

### AC-MON-001: Health Check Script Running

**Given** the gpu-health.sh script is deployed to `/opt/klai/scripts/`
**When** the cron job executes every 60 seconds
**Then** it checks HTTP health endpoints for all three tunneled services
**And** pushes status to Uptime Kuma on success

### AC-MON-002: Alert on Service Failure

**Given** the health check script is running and Uptime Kuma monitor is configured
**When** I stop the SSH tunnel service (simulating failure)
**Then** within 2 minutes Uptime Kuma shows the GPU tunnel monitor as DOWN

### AC-MON-003: Rollback Procedure Documented

**Given** the rollback procedure document exists
**When** I review it
**Then** it contains step-by-step instructions to:
  1. Stop the GPU tunnel service
  2. Re-enable old GPU services in docker-compose.yml
  3. Revert consumer environment variables
  4. Start old GPU services
  5. Restart consumer services
  6. Verify end-to-end functionality
**And** the target completion time is under 15 minutes

### AC-MON-004: Rollback Drill Successful

**Given** the migration is complete and running on Vast.ai
**When** I execute the full rollback procedure
**Then** all consumer services are restored to using local Docker GPU services within 15 minutes
**And** end-to-end flows (embed, rerank, sparse, transcribe) pass verification
**And** when I re-migrate to Vast.ai, all services come back online

---

## Module 5: Data Security

### AC-SEC-001: No Customer Data on Vast.ai

**Given** the Vast.ai GPU box is operational
**When** I audit the data flowing through the inference services
**Then** only test/staging data is processed
**And** no customer-identifiable content appears in GPU box logs or model inputs

### AC-SEC-002: Risk Acceptance Documented

**Given** the RTX 3090 does not support NVIDIA Confidential Computing
**When** I review the SPEC documentation
**Then** this limitation is explicitly acknowledged
**And** the test-only scope is documented as the mitigation

### AC-SEC-003: SSH Keys Not in Version Control

**Given** the GPU tunnel SSH keypair exists on core-01
**When** I search the git repository for the private key content or filename
**Then** no matches are found
**And** the key path `/opt/klai/gpu-tunnel-key` is not referenced in any committed configuration file with the actual key material

---

## End-to-End Validation Scenarios

### E2E-001: Document Ingestion Pipeline

**Given** the full migration is complete and all tunnels are active
**When** I upload a test PDF document to the knowledge-ingest service
**Then** the document is chunked and embedded via Infinity (localhost:7997)
**And** sparse embeddings are generated via BGE-M3 (localhost:8001)
**And** the document is indexed successfully
**And** latency is within acceptable bounds (< 2x pre-migration latency)

### E2E-002: Search Query Pipeline

**Given** test documents have been ingested via E2E-001
**When** I execute a search query via retrieval-api
**Then** the query is embedded via Infinity (localhost:7997)
**And** results are reranked via Infinity reranker (localhost:7997)
**And** sparse matching occurs via BGE-M3 (localhost:8001)
**And** relevant results are returned within acceptable latency

### E2E-003: Audio Transcription Pipeline

**Given** the migration is complete and the whisper tunnel is active
**When** I upload a test audio file to scribe-api
**Then** the audio is transcribed via faster-whisper (localhost:8000)
**And** a valid transcription text is returned
**And** transcription latency is within acceptable bounds (< 2x pre-migration latency)

### E2E-004: Tunnel Recovery Under Load

**Given** consumer services are actively processing requests
**When** the SSH tunnel drops and autossh reconnects
**Then** in-flight requests may fail (accepted)
**And** subsequent requests succeed within 60 seconds of tunnel recovery
**And** no consumer service crashes or enters an unrecoverable state

---

## Quality Gates

| Gate | Criteria | Pass Condition |
|------|----------|---------------|
| Security | All services bound to 127.0.0.1 | AC-GPU-002 passes |
| Security | SSH-only communication | AC-SSH-001 passes |
| Security | No customer data | AC-SEC-001 passes |
| Reliability | Auto-reconnect works | AC-SSH-003 passes |
| Reliability | Supervisord auto-restart | AC-GPU-004 passes |
| Functionality | All E2E flows pass | E2E-001 through E2E-003 pass |
| Operability | Monitoring active | AC-MON-001, AC-MON-002 pass |
| Recoverability | Rollback tested | AC-MON-004 passes |

---

## Verification Methods

| Method | Scope | Tools |
|--------|-------|-------|
| SSH port scan | AC-GPU-002, AC-GPU-005 | `ss -tlnp`, `nmap` |
| HTTP health checks | AC-SSH-004, AC-MIG-001 | `curl` |
| Process inspection | AC-GPU-001, AC-GPU-004 | `supervisorctl`, `ps` |
| GPU monitoring | AC-GPU-006 | `nvidia-smi` |
| Log analysis | AC-MIG-001, AC-SEC-001 | `docker logs`, service logs |
| Timed drill | AC-MON-004 | Stopwatch + procedure |
| Git diff | AC-MIG-004 | `git diff` |
