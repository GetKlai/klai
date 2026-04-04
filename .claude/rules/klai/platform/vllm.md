---
paths:
  - "deploy/docker-compose*.yml"
  - "klai-infra/ai-01/**"
---
# vLLM

## GPU memory split (CRIT)
Two instances on one H100 (80GB):
- 32B: `--gpu-memory-utilization 0.55` (44GB ceiling: 33GB weights + 11GB KV cache)
- 8B: `--gpu-memory-utilization 0.40` (32GB ceiling: 9GB weights + 23GB KV cache)
- Combined ~76GB, leaves ~4GB for CUDA overhead + Whisper.

## Sequential startup (CRIT)
Never start in parallel — memory accounting bug.
1. Start 32B → wait healthy
2. Start 8B → wait healthy
3. Start Whisper last
Use `depends_on` with `condition: service_healthy` in compose.

## NVIDIA MPS
Add `--enforce-eager` to the smaller (8B) instance when MPS is enabled.
CUDAGraph + MPS causes illegal memory access on some configs.

## CUDA version
faster-whisper (CTranslate2) requires CUDA 12 + cuDNN 9. Verify before deploying:
```bash
nvidia-smi
cat /usr/local/cuda/include/cudnn_version.h | grep CUDNN_MAJOR
```
