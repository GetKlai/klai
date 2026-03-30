# Research: GPU Inference Service Migration

## Context

Klai migrates GPU-heavy inference services from core-01 (Docker Compose, internal network) to a Vast.ai GPU instance (RTX 3090, 24GB VRAM, Belgium). This is a test/staging setup; production will move to dedicated GPU hardware.

## Current Security Posture (core-01)

### Network Isolation

All four inference services run on `klai-net` Docker network with **NO external port mappings**:

| Service | Port | Network | External Exposure |
|---------|------|---------|-------------------|
| TEI (dense embeddings) | 8080 | klai-net | None |
| BGE-M3 sparse | 8001 | klai-net | None |
| Whisper server | 8000 | klai-net | None |
| Infinity reranker | 7997 | klai-net | None |

Source: `deploy/docker-compose.yml` lines 561-569, 571-592, 741-753, 922-942

### Authentication: NONE

No inference service has any authentication mechanism:
- TEI: No API key support — `embedder.py:45` sends plain HTTP POST to `/embed`
- BGE-M3 sparse: No auth — `sparse_embedder.py:30` plain POST to `/embed_sparse_batch`
- Whisper: No auth — `providers.py:55` multipart POST to `/v1/audio/transcriptions`
- Infinity reranker: LibreChat sends `JINA_API_KEY: "klai-internal"` but Infinity ignores it

Source: `deploy/knowledge-ingest/knowledge_ingest/embedder.py`, `sparse_embedder.py`, `klai-retrieval-api/retrieval_api/services/reranker.py`, `klai-scribe/scribe-api/app/services/providers.py`

### Encryption in Transit: NONE

All service-to-service calls use plain HTTP over Docker network. No TLS between containers.

### Current Security Model

Security relies **entirely** on Docker network isolation. No container on `klai-net` needs authentication to call any inference service. This is acceptable because:
- No ports are exposed to the host
- Only trusted application containers can reach inference services
- Caddy terminates TLS for all public-facing endpoints

## Migration Threat Model

### CRITICAL: Public Internet Exposure

Moving to Vast.ai means inference services get **publicly routable IP:port combinations**. Current "no auth" model becomes an open API on the internet.

**Threat 1: Unauthorized Access**
- Anyone who discovers IP:port can embed documents, transcribe audio, rerank queries
- Port scanning will find the services within hours
- Attacker can use free GPU compute for their own embedding/transcription workloads

**Threat 2: Data Exfiltration via Request Inspection**
- Documents sent for embedding contain customer business content
- Audio files contain meeting recordings (potentially confidential)
- Search queries reveal what users are looking for
- All data travels unencrypted over public internet

**Threat 3: Denial of Service**
- No rate limiting on inference services
- Attacker can flood with large audio files (whisper) or massive batch embeds (TEI)
- GPU OOM or service crash affects all Klai users

**Threat 4: Man-in-the-Middle**
- Plain HTTP between core-01 and GPU box
- Network path: core-01 (Hetzner DE) → public internet → Vast.ai (Belgium)
- ISP or network-level interception possible

### MODERATE: Vast.ai Shared Infrastructure

**Threat 5: GPU Memory Isolation**
- Vast.ai runs containers on shared physical hosts
- GPU memory isolation depends on NVIDIA driver (CUDA MPS/MIG)
- RTX 3090 does NOT support MIG (only A100/H100 do)
- Theoretical: another tenant's container could probe GPU memory
- Practical risk: LOW for embeddings (vectors are not secrets), MODERATE for audio content

**Threat 6: Host Operator Access**
- Physical host operator can inspect container filesystem and network traffic
- Vast.ai ToS: user data remains user's property, but no formal DPA
- Belgium = EU jurisdiction (GDPR applies)

### LOW: Service-Level Risks

**Threat 7: Model Poisoning**
- If attacker gains write access to model cache, they could swap model weights
- Impact: corrupted embeddings would degrade search quality silently
- Mitigation: model checksums, read-only model volumes

## Mitigation Options Analyzed

### Option A: SSH Tunnel (Recommended for test phase)

```
core-01 ──SSH tunnel──> GPU box
         encrypted       services on localhost only
```

- Core-01 opens SSH tunnels to GPU box for each service port
- Services bind to `127.0.0.1` (not `0.0.0.0`) — no public exposure at all
- All traffic encrypted via SSH
- No changes needed to inference services
- No additional software on GPU box
- Autossh for automatic reconnection

**Pros:** Zero public exposure, encrypted transit, no code changes
**Cons:** SSH tunnel overhead (~5% latency), single point of failure, needs monitoring

### Option B: WireGuard VPN

```
core-01 ──WireGuard──> GPU box
         encrypted      private network
```

- Point-to-point VPN between core-01 and GPU box
- Services accessible only via VPN IP
- Kernel-level encryption, lower overhead than SSH

**Pros:** Lower latency than SSH tunnel, kernel-level performance
**Cons:** Requires WireGuard install on both ends, Vast.ai may restrict kernel modules

### Option C: Nginx Reverse Proxy with API Key

```
internet ──> nginx:5000 ──> services
             API key check
```

- Nginx on GPU box checks `X-API-Key` header before proxying
- Services bind to localhost, nginx is the only public listener
- API key shared between core-01 and GPU box via env var

**Pros:** Standard pattern, easy to implement
**Cons:** Still transmits data over public internet (encrypted if HTTPS), API key can leak

### Option D: Tailscale (Zero-config VPN)

- Tailscale installs as userspace, no kernel modules needed
- Works inside Vast.ai containers
- Automatic key rotation, ACLs

**Pros:** Easiest setup, works in containers, encrypted
**Cons:** Dependency on Tailscale service, free tier limited to 100 devices

## Recommendation

**For test/staging (current Vast.ai setup):**

1. **SSH tunnel** (Option A) — simplest, zero public exposure, no extra software
2. Services bind to `127.0.0.1` only — even if tunnel drops, services unreachable
3. `autossh` on core-01 maintains persistent tunnels
4. Health check monitors tunnel status

**For production (future dedicated GPU server):**

1. **WireGuard VPN** between core-01 and GPU server
2. Add API key authentication to a lightweight reverse proxy (defense in depth)
3. Consider mTLS for service-to-service communication

## Data Flow After Migration

```
User → Caddy (TLS) → research-api/scribe-api/retrieval-api (core-01)
                           │
                           │ SSH tunnel (encrypted)
                           ▼
                      GPU box (localhost only)
                           ├── Infinity :7997 (embeddings + reranker)
                           ├── BGE-M3 sparse :8001
                           └── faster-whisper :8000
```

## GDPR Assessment

| Aspect | Status |
|--------|--------|
| Data location | Belgium (EU) — compliant |
| Data processor | Vast.ai is infrastructure provider, not data processor |
| Data in transit | Encrypted via SSH tunnel |
| Data at rest | Transient only (inference services don't persist data) |
| Host operator access | Theoretical risk — acceptable for test, review for production |

## Encryption Options Investigated

### NVIDIA Confidential Computing (CC)

Hardware-based TEE (Trusted Execution Environment) with AES-GCM 256-bit encryption of GPU memory. The host operator cannot read GPU memory even with physical access.

**Verdict: NOT AVAILABLE on RTX 3090.** Requires H100, H200, or Blackwell GPUs. This is a hardware feature — no software workaround exists.

### Homomorphic Encryption (FHE)

Allows computation on encrypted data without decryption. In theory, the GPU could run inference on encrypted embeddings/audio without ever seeing plaintext.

**Verdict: IMPRACTICAL.** Current FHE implementations are 4-5 orders of magnitude slower than plaintext inference (10,000-100,000x). A 50ms embedding call would take 500+ seconds.

### Software-Level GPU Memory Encryption

No software can protect GPU memory from the physical host operator. NVIDIA driver tools allow direct GPU memory inspection from the host level.

**Verdict: IMPOSSIBLE without hardware TEE support.**

### Conclusion

On an RTX 3090 on Vast.ai, **there is no practical way to protect data in GPU memory from the host operator**. The only mitigations are:

1. **Transit encryption** (SSH tunnels) — protects data on the wire ✓
2. **No data persistence** — inference services don't store data ✓
3. **Accept residual risk** for test phase with own data only
4. **Production requirement**: Own hardware (Hetzner dedicated GPU) where Klai controls the physical machine

## Files Referenced

| File | Lines | Finding |
|------|-------|---------|
| `deploy/docker-compose.yml` | 561-592 | TEI + sparse service definitions |
| `deploy/docker-compose.yml` | 741-753 | Whisper service definition |
| `deploy/docker-compose.yml` | 922-942 | Infinity reranker definition |
| `deploy/knowledge-ingest/.../embedder.py` | 45 | No auth in TEI client |
| `deploy/knowledge-ingest/.../sparse_embedder.py` | 30 | No auth in sparse client |
| `klai-retrieval-api/.../reranker.py` | 1-65 | No auth in reranker client |
| `klai-scribe/.../providers.py` | 55 | No auth in whisper client |
| `deploy/caddy/Caddyfile` | 161-191 | Public routes, inference NOT exposed |
