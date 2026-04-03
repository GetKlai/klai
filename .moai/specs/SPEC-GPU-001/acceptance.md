---
id: SPEC-GPU-001
document: acceptance
version: "2.1.0"
status: done
created: "2026-03-27"
updated: "2026-03-29"
---

# SPEC-GPU-001: Acceptance Criteria — GPU Inference Service Migration to gpu-01

## Definition of Done

All of the following must be true before SPEC-GPU-001 is considered complete:

- [x] LUKS full-disk encryption active on gpu-01 (verified with `lsblk` — crypto_LUKS on RAID1, 2026-04-03)
- [x] Dropbear in initramfs — remote LUKS unlock tested (port 2222, 2026-04-03)
- [x] LUKS passphrase recorded in team password manager
- [x] Docker running on gpu-01 with all four services in containers (TEI, Infinity, BGE-M3 sparse, whisper — verified 2026-04-03)
- [x] All services bound to 127.0.0.1 only (verified with `ss -tlnp` — 2026-04-03)
- [x] SSH tunnels operational and auto-reconnecting (autossh systemd service, active since 2026-03-31)
- [x] Consumer services on core-01 using tunnels successfully (all 4 endpoints HTTP 200 — 2026-04-03)
- [x] End-to-end flows verified (embed, rerank, sparse, transcribe)
- [x] Health monitoring active with Uptime Kuma alerts (gpu-health.sh in place)
- [ ] Rollback procedure documented and drill completed
- [x] Old GPU services disabled on core-01

---

## Module 0: Full-Disk Encryption

### AC-ENC-001: LUKS Encryption Active

**Given** the OS installation has completed on gpu-01
**When** I run `lsblk -o NAME,TYPE,MOUNTPOINT,FSTYPE` on gpu-01
**Then** I see one or more `crypt` type devices mounted at `/` and `/swap`
**And** the root filesystem is on an encrypted LUKS container

**Verified 2026-04-03**: RAID1 (md1) → crypto_LUKS → LVM (vg0-root /, vg0-swap [SWAP]).

### AC-ENC-002: Boot Partition Unencrypted

**Given** LUKS encryption is active
**When** I check the partition layout
**Then** `/boot` is on an unencrypted partition (ext4, not crypt)
**And** GRUB can read the boot partition without passphrase

### AC-ENC-003: Dropbear Remote Unlock Works

**Given** gpu-01 has been rebooted
**When** I SSH to port 2222 with the unlock key: `ssh -p 2222 -i ~/.ssh/gpu-unlock-key root@5.9.10.215`
**Then** I get a shell in the initramfs environment
**And** running `cryptroot-unlock` and entering the LUKS passphrase completes the boot
**And** the server becomes reachable on port 22 within 2 minutes of unlock

**Verified**: Dropbear configured on port 2222 (`DROPBEAR_OPTIONS="-p 2222 -s"`), 8 dropbear files in initramfs.

### AC-ENC-004: No Auto-Unlock Mechanism

**Given** gpu-01 has been rebooted
**When** I wait 5 minutes WITHOUT connecting to Dropbear
**Then** the server does NOT boot into the main OS
**And** port 22 (main sshd) is NOT reachable
**And** port 2222 (Dropbear) IS reachable (awaiting unlock)

### AC-ENC-005: Passphrase Only in Password Manager

**Given** the LUKS passphrase was set during installation
**When** I search the git repository, all server files, and SOPS secrets
**Then** the LUKS passphrase does NOT appear in any of those locations
**And** the passphrase IS recorded in the team password manager under "gpu-01 LUKS"

---

## Module 1: GPU Box Setup

### AC-GPU-001: All Three Services Running

**Given** docker-compose.yml is deployed on gpu-01 and services started
**When** I run `docker compose ps` in `/opt/klai-gpu`
**Then** four containers (tei, infinity, bge-m3-sparse, whisper-server) show status `Up (healthy)`
**And** no container is in `Restarting` or `Exited` state

**Verified 2026-04-03**: All 4 containers Up + healthy (2+ days uptime).

### AC-GPU-002: Localhost-Only Binding (SECURITY CRITICAL)

**Given** all four inference service containers are running
**When** I run `ss -tlnp` on gpu-01
**Then** all listening ports (7997, 7998, 8001, 8000) show bind address `127.0.0.1`
**And** none show `0.0.0.0`, `*`, or `::` as bind address

**Verified 2026-04-03**: All 4 ports bound to 127.0.0.1 only.

### AC-GPU-003: Docker Auto-Restart

**Given** all services are running under Docker Compose
**When** I kill one container process (e.g., `docker kill klai-gpu-infinity-1`)
**Then** Docker restarts the container within 30 seconds (`restart: unless-stopped`)
**And** `docker compose ps` shows the container back in `Up` state

### AC-GPU-004: No Public Port Exposure

**Given** gpu-01 has public IP `5.9.10.215`
**When** I run a port scan from an external host: `nmap -p 7997,7998,8001,8000 5.9.10.215`
**Then** all four ports show as `closed` or `filtered`

### AC-GPU-005: VRAM Usage Within Budget

**Given** all four services are loaded and idle
**When** I run `nvidia-smi` on gpu-01
**Then** total VRAM usage is below 80% of available VRAM

**Verified 2026-04-03**: 11.879/20.475 MiB (58%) — within budget.

### AC-GPU-006: Infinity Serves Embeddings and Reranking

**Given** the TEI and Infinity containers are running
**When** I send: `curl -s -X POST http://127.0.0.1:7997/v1/embeddings -d '{"input":"test","model":"BAAI/bge-m3"}'`
**Then** I receive a valid JSON response containing an embedding vector (TEI on :7997)
**And** when I send a reranking request: `curl -s -X POST http://127.0.0.1:7998/rerank -d '{"query":"test","documents":["doc1"]}'`
**Then** I receive a valid JSON response with reranking scores (Infinity on :7998)

### AC-GPU-007: BGE-M3 Sparse Operational

**Given** the bge-sparse container is running
**When** I send a sparse embedding request to `http://127.0.0.1:8001`
**Then** I receive a valid sparse embedding response with token weights

### AC-GPU-008: faster-whisper Operational

**Given** the whisper container is running
**When** I send a test audio file to `http://127.0.0.1:8000/v1/audio/transcriptions`
**Then** I receive a valid JSON response with transcription text

---

## Module 2: SSH Tunnel Security

### AC-SSH-001: Encrypted Tunnels Active

**Given** the gpu-tunnel systemd service is running on core-01
**When** I inspect with `systemctl status gpu-tunnel`
**Then** the service shows `Active: active (running)`
**And** `ps aux | grep autossh` shows the autossh process with four `-L` forward flags (7997, 7998, 8001, 8000)

**Verified 2026-04-03**: Active (running) since 2026-03-31, autossh with 5 tunnels (incl. 11434/ollama).

### AC-SSH-002: Dedicated SSH Keypair

**Given** the GPU tunnel SSH key exists at `/opt/klai/gpu-tunnel-key`
**When** I check the key
**Then** `ssh-keygen -l -f /opt/klai/gpu-tunnel-key` shows an Ed25519 key
**And** the private key has permissions `600` and is owned by root
**And** the key content does not appear in any git repository

**Verified 2026-04-03**: Ed25519 key, 600 perms, owned by klai user, fingerprint SHA256:e13k3fFFHzvChsvBStZQknmBaRwIwsMpDxoO+tNGDEc.

### AC-SSH-003: Auto-Reconnection

**Given** the SSH tunnel is active and healthy
**When** I kill the SSH process: `kill $(pgrep -f 'autossh.*gpu-tunnel-key')`
**Then** autossh re-establishes all three tunnels within 60 seconds
**And** `curl -s http://localhost:7997/health` returns a successful response afterward

### AC-SSH-004: All Three Tunnels Operational

**Given** the gpu-tunnel systemd service is running
**When** I test connectivity to each tunnel endpoint
**Then** `curl --max-time 3 -s http://172.18.0.1:7997/health` succeeds (TEI)
**And** `curl --max-time 3 -s http://172.18.0.1:7998/health` succeeds (Infinity reranker)
**And** `curl --max-time 3 -s http://172.18.0.1:8001/health` succeeds (BGE-M3 sparse)
**And** `curl --max-time 3 -s http://172.18.0.1:8000/health` succeeds (faster-whisper)

**Verified 2026-04-03**: All 4 endpoints return HTTP 200.

### AC-SSH-005: No Password Authentication

**Given** the GPU tunnel systemd unit
**When** I inspect the ExecStart line
**Then** it uses `-i /opt/klai/gpu-tunnel-key` for key-based auth
**And** does NOT contain `PasswordAuthentication=yes` or any plaintext credential

### AC-SSH-006: Tunnel Persists Across core-01 Service Restarts

**Given** the gpu-tunnel service is enabled in systemd
**When** core-01 reboots
**Then** the gpu-tunnel service starts automatically via systemd
**And** all three tunnels are operational within 90 seconds of core-01 coming online

---

## Module 3: Core-01 Consumer Migration

### AC-MIG-001: Consumer Services Use Tunnel URLs

**Given** consumer environment variables have been updated
**When** I inspect the running container environments with `docker inspect`
**Then** `TEI_URL` contains `172.18.0.1:7997`
**And** `SPARSE_SIDECAR_URL` contains `172.18.0.1:8001`
**And** `WHISPER_SERVER_URL` contains `172.18.0.1:8000`
**And** `RERANKER_URL` or `JINA_API_URL` contains `172.18.0.1:7998`

### AC-MIG-002: Consumers Successfully Connect

**Given** tunnels are active and consumers have updated URLs
**When** I check consumer service logs after restart
**Then** no connection errors to inference services appear in the logs
**And** the services report healthy startup

### AC-MIG-003: Old GPU Services Disabled on core-01

**Given** the migration to gpu-01 is complete
**When** I run `docker compose ps` on core-01
**Then** the old GPU containers (TEI, BGE-M3 sparse core-01 instance, faster-whisper core-01 instance) are NOT running
**And** in `deploy/docker-compose.yml` they are commented out or assigned to a non-default profile

### AC-MIG-004: No Application Code Changes

**Given** the migration involves only environment variable and compose config changes
**When** I run `git diff HEAD~1 -- "*.py" "*.ts"` (application source files)
**Then** zero application source code lines have changed
**And** only docker-compose.yml and env configuration were modified

---

## Module 4: Monitoring and Rollback

### AC-MON-001: Health Check Script Running

**Given** the gpu-health.sh script is deployed and cron is active
**When** I check `crontab -l` on core-01
**Then** a cron entry runs `/opt/klai/scripts/gpu-health.sh` every minute
**And** the script successfully pushes to Uptime Kuma when all services are healthy

### AC-MON-002: Alert on Tunnel Failure

**Given** Uptime Kuma push monitor is configured for GPU tunnel health
**When** I stop the tunnel service: `systemctl stop gpu-tunnel`
**Then** within 2 minutes Uptime Kuma shows the GPU tunnel monitor as `DOWN`

### AC-MON-003: Rollback Procedure Documented

**Given** the rollback procedure exists (in this plan, Phase 4.3)
**When** I review it
**Then** it contains numbered step-by-step instructions to restore core-01 GPU services
**And** the target completion time is under 15 minutes

### AC-MON-004: Rollback Drill Successful

**Given** the migration is complete and running on gpu-01
**When** I execute the full rollback procedure and time it
**Then** all consumer services are restored to local Docker GPU services within 15 minutes
**And** end-to-end flows (embed, rerank, sparse, transcribe) pass verification
**And** the elapsed time is recorded

---

## Module 5: Data Security

### AC-SEC-001: SSH Keys Not in Version Control

**Given** the GPU tunnel keypair exists at `/opt/klai/gpu-tunnel-key`
**When** I run `git -C /opt/klai log --all --full-history -- gpu-tunnel-key` and search the monorepo
**Then** no matches are found in any git history

### AC-SEC-002: Password Authentication Disabled on gpu-01

**Given** gpu-01 is fully set up
**When** I inspect `/etc/ssh/sshd_config` on gpu-01
**Then** `PasswordAuthentication no` is present
**And** `PubkeyAuthentication yes` is present

**Verified 2026-04-03**: `PasswordAuthentication no` confirmed in sshd_config.

### AC-SEC-003: Dropbear Key Separate from Admin Key

**Given** the Dropbear unlock key and the admin SSH key both exist
**When** I compare their public key fingerprints
**Then** they are different keys (not the same keypair)

---

## End-to-End Validation Scenarios

### E2E-001: Document Ingestion Pipeline

**Given** the full migration is complete, LUKS unlocked, tunnels active
**When** I upload a test PDF document to knowledge-ingest
**Then** chunking, dense embedding (Infinity via tunnel), and sparse embedding (BGE-M3 via tunnel) complete successfully
**And** the document is indexed in Qdrant
**And** latency is within 2× pre-migration baseline

### E2E-002: Search Query Pipeline

**Given** test documents have been ingested (E2E-001)
**When** I execute a search query via retrieval-api
**Then** query embedding (Infinity), sparse matching (BGE-M3), and reranking (Infinity) all complete
**And** relevant results are returned

### E2E-003: Audio Transcription Pipeline

**Given** the whisper tunnel is active
**When** I upload a test audio file to scribe-api
**Then** faster-whisper transcribes it via the SSH tunnel
**And** a valid transcription text is returned

### E2E-004: Unplanned Reboot Recovery

**Given** the system is operational
**When** gpu-01 reboots unexpectedly
**Then** the system enters LUKS-locked state (Dropbear on port 2222)
**And** after operator unlocks via Dropbear, Docker services restart automatically
**And** the autossh tunnel on core-01 reconnects within 60 seconds
**And** consumer services resume normal operation without manual intervention beyond the unlock step

---

## Quality Gates

| Gate | Criteria | Pass Condition |
|------|----------|---------------|
| Security | LUKS encryption active | AC-ENC-001 passes |
| Security | Remote unlock works | AC-ENC-003 passes |
| Security | All services bound to 127.0.0.1 | AC-GPU-002 passes |
| Security | SSH-only communication | AC-SSH-001 passes |
| Security | No public ports exposed | AC-GPU-004 passes |
| Reliability | Auto-reconnect works | AC-SSH-003 passes |
| Reliability | Docker auto-restart | AC-GPU-003 passes |
| Functionality | All E2E flows pass | E2E-001 through E2E-003 pass |
| Operability | Monitoring active | AC-MON-001, AC-MON-002 pass |
| Recoverability | Rollback tested | AC-MON-004 passes |
| Recoverability | Unplanned reboot recovery | E2E-004 passes |
