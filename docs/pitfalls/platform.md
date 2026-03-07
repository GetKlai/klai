# Platform Pitfalls

> LiteLLM, LibreChat, vLLM, Zitadel, Caddy, Meilisearch — Klai AI stack.
> Most entries are derived from the compatibility review in `klai-website/docs/platform-beslissingen.md`.

---

## platform-litellm-vllm-provider-prefix

**Severity:** HIGH

**Trigger:** Configuring LiteLLM to route to vLLM instances

The provider prefix must be `hosted_vllm/`, not `openai/`. Using `openai/` causes routing errors.

**Wrong:**
```yaml
model: openai/qwen3-32b
api_base: http://localhost:8001/v1
```

**Correct:**
```yaml
model: hosted_vllm/qwen3-32b
api_base: http://localhost:8001/v1
```

**Source:** `platform-beslissingen.md` — Compatibility Review: LiteLLM to vLLM

---

## platform-litellm-drop-params

**Severity:** HIGH

**Trigger:** Setting up LiteLLM configuration for vLLM backends

`drop_params: true` must be set in `litellm_settings`. vLLM does not accept all OpenAI parameters and will error without this setting.

```yaml
litellm_settings:
  drop_params: true
```

**Source:** `platform-beslissingen.md` — Compatibility Review: LiteLLM to vLLM

---

## platform-vllm-gpu-memory-utilization

**Severity:** CRIT

**Trigger:** Configuring `--gpu-memory-utilization` for two vLLM instances on one H100

`--gpu-memory-utilization` is a ceiling on total VRAM, not a split. Setting it too low leaves no room for KV cache and causes OOM or crash on startup.

**Wrong (original values):**
```
32B instance: --gpu-memory-utilization 0.41  # = 32.8 GB ceiling, barely fits weights
8B instance:  --gpu-memory-utilization 0.12  # = 9.6 GB, barely fits weights
```

**Correct:**
```
32B instance: --gpu-memory-utilization 0.55  # = 44 GB ceiling (33 GB weights + 11 GB KV cache)
8B instance:  --gpu-memory-utilization 0.40  # = 32 GB ceiling (9 GB weights + 23 GB KV cache)
# Combined: ~76 GB of 80 GB. Leaves ~4 GB for CUDA overhead and Whisper.
```

**Source:** `platform-beslissingen.md` — Compatibility Review: vLLM gpu-memory-utilization

---

## platform-vllm-sequential-startup

**Severity:** CRIT

**Trigger:** Starting two vLLM instances on one GPU

vLLM has a memory accounting bug where parallel startup causes the second instance to see the first's VRAM as occupied. Always start sequentially.

**Startup order:**
1. Start 32B instance (Qwen3-32B)
2. Wait for it to be healthy
3. Start 8B instance (Qwen3-8B)
4. Wait for it to be healthy
5. Then start Whisper (CTranslate2)

**Implementation:** Use `depends_on` with health checks in Docker Compose, or sequential systemd/startup scripts.

**Source:** `platform-beslissingen.md` — Compatibility Review: vLLM two instances on one GPU

---

## platform-vllm-mps-enforce-eager

**Severity:** HIGH

**Trigger:** Running vLLM with NVIDIA MPS enabled

vLLM CUDAGraph combined with MPS can cause instability (illegal memory access) on some configurations.

**Prevention:** Add `--enforce-eager` to the smaller (8B) vLLM instance to disable CUDAGraph.

```bash
vllm serve qwen3-8b ... --enforce-eager
```

**Source:** `platform-beslissingen.md` — Compatibility Review: NVIDIA MPS setup

---

## platform-librechat-oidc-reuse-tokens

**Severity:** CRIT

**Trigger:** Configuring LibreChat OIDC with `OPENID_REUSE_TOKENS=true`

This setting breaks existing users. Do not set it on any deployment that has existing user accounts.

**Prevention:**
```bash
# .env template for LibreChat containers:
OPENID_REUSE_TOKENS=false   # Never set to true on non-fresh deployments
```

**Source:** `platform-beslissingen.md` — LibreChat OIDC known issues (GitHub #9303)

---

## platform-librechat-username-claim

**Severity:** HIGH

**Trigger:** Setting up LibreChat OIDC integration with Zitadel

Without explicit configuration, LibreChat falls back to `given_name` as the username, which causes display and identity issues.

**Required setting in all LibreChat container `.env` files:**
```bash
OPENID_USERNAME_CLAIM=preferred_username
```

**Source:** `platform-beslissingen.md` — LibreChat OIDC known issues (GitHub #8672)

---

## platform-librechat-logout-no-zitadel-session

**Severity:** HIGH

**Trigger:** Implementing logout in the customer portal

LibreChat logout does NOT call the Zitadel `end_session` endpoint. After LibreChat logout, the Zitadel session remains active. Users can immediately log back in without re-authenticating.

**Prevention:**
Build a custom logout flow that:
1. Logs out of LibreChat
2. Redirects to the Zitadel end-session endpoint with `post_logout_redirect_uri`
3. Then redirects back to the portal login

**Source:** `platform-beslissingen.md` — Auth: Zitadel + LibreChat + FastAPI + React SPA

---

## platform-grafana-victorialogs-loki-incompatible

**Severity:** HIGH

**Trigger:** Adding VictoriaLogs as a datasource in Grafana

The generic Loki datasource plugin does NOT work with VictoriaLogs. LogsQL (VictoriaLogs) and LogQL (Loki) are incompatible query languages.

**Prevention:** Install the dedicated plugin:
```bash
GF_INSTALL_PLUGINS=victoriametrics-logs-datasource
```

Configure the datasource using the `victoriametrics-logs-datasource` plugin type, not the Loki plugin.

**Source:** `platform-beslissingen.md` — Monitoring: Grafana datasource

---

## platform-caddy-cloud86-no-plugin

**Severity:** HIGH (historical — DNS has been migrated)

**Trigger:** Setting up wildcard TLS for `*.getklai.com` with Caddy

Cloud86 (former DNS provider for getklai.com) has no Caddy DNS plugin. Caddy requires a DNS-01 ACME challenge to issue wildcard certificates. Without a plugin, wildcard TLS is not possible.

**Resolution (implemented March 2026):** DNS migrated from Cloud86 to Hetzner DNS.
- Hetzner DNS is free, fully European, GDPR-compliant
- Caddy plugin in use: `github.com/caddy-dns/hetzner`
- Custom Caddy image: `caddy-hetzner:latest` (built via `xcaddy build --with github.com/caddy-dns/hetzner`)
- Wildcard cert `*.getklai.com` is active via Let's Encrypt DNS-01 challenge

**Do not revert DNS to Cloud86 or any provider without a Caddy plugin.**

**Source:** `platform-beslissingen.md` — Per-Tenant Routing: Caddy + Wildcard DNS

---

## platform-caddy-not-auto-routing

**Severity:** HIGH

**Trigger:** Assuming Caddy automatically routes to new tenant containers

Caddy does NOT automatically discover new Docker containers. Provisioning a new tenant does not automatically make their subdomain work.

**Required architecture:**
- Caddy has one static `*.getklai.com` block
- A FastAPI Tenant Router dispatcher reads tenant registration from the database and proxies to the correct container via Docker network hostname
- New tenants are registered in the database; no Caddy config reload needed

**Do not** attempt to update Caddy config via the Admin API on every tenant provisioning.

**Source:** `platform-beslissingen.md` — Per-Tenant Routing: Tenant Router

---

## platform-rag-api-non-lite-image

**Severity:** HIGH

**Trigger:** Deploying LibreChat RAG with HuggingFace TEI embeddings (Phase 2)

The lite RAG API image does NOT support TEI embeddings. The full image is required.

**Correct image:**
```bash
ghcr.io/danny-avila/librechat-rag-api-dev:latest
# NOT: ghcr.io/danny-avila/librechat-rag-api-dev-lite:latest
```

**Also:** `EMBEDDINGS_MODEL` must be the TEI service URL, not a model name.

**Source:** `platform-beslissingen.md` — RAG Stack: LibreChat rag_api + HuggingFace TEI

---

## platform-whisper-cuda-version

**Severity:** HIGH

**Trigger:** Deploying faster-whisper on core-01 or ai-01

CTranslate2 (the Whisper runtime) requires CUDA 12 + cuDNN 9. Version mismatch is the most common deployment failure.

**Prevention:**
1. Verify CUDA version before deploying: `nvidia-smi`
2. Verify cuDNN version: `cat /usr/local/cuda/include/cudnn_version.h | grep CUDNN_MAJOR`
3. Use a base Docker image that already pins `cuda:12.x-cudnn9`

**Source:** `platform-beslissingen.md` — GPU Resource Management: faster-whisper on H100

---

## See Also

- [patterns/platform.md](../patterns/platform.md) - Correct platform configuration patterns
- [platform-beslissingen.md](../../../klai-website/docs/platform-beslissingen.md) - Full compatibility review
