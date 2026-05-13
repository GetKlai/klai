# Base image bumped from 12.6.3 to 12.9.1 (latest 12.x patch as of 2026-05).
# This brings 9 months of accumulated Ubuntu 24.04 security updates into the
# image — most importantly the gnupg2 fix for CVE-2025-68973 (out-of-bounds
# write in keybox parser) which previously surfaced 10 HIGH alerts in Trivy
# (gpg, gpgv, gpgsm, dirmngr, gnupg-utils, gnupg-l10n, gpg-agent, gpgconf,
# gpg-wks-server, gpg-wks-client — all the same CVE multiplied across
# package members).
#
# Major version stays at CUDA 12.x so cuDNN/runtime ABI compatibility with
# faster-whisper / CTranslate2 is preserved. CUDA 13.x bump is out of scope
# for SPEC-CI-TRIVY-POLICY-001 (separate effort, requires GPU smoke-test).
FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04

# `apt-get purge gnupg gnupg2` removes unused gpg attack surface. whisper-
# server never invokes gpg during inference; gnupg only ships transitively
# in the CUDA base because Ubuntu's image-build pipeline installs it for
# package signature verification at provisioning time. After provisioning
# the binary has no purpose in the runtime container, so we remove the
# entire family. Combined with the 12.9.1 base bump this defends against
# CVE-2025-68973 by both patching AND removing the affected binaries.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    ffmpeg \
    libsndfile1 \
    && apt-get purge -y --auto-remove gnupg gnupg2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
ENV UV_BREAK_SYSTEM_PACKAGES=1
RUN pip install --no-cache-dir --break-system-packages uv && \
    uv pip install --system --no-cache -r pyproject.toml

COPY logging_setup.py main.py ./

ENV WHISPER_MODEL=large-v3-turbo
ENV WHISPER_DEVICE=cuda
ENV WHISPER_COMPUTE_TYPE=float16
ENV WHISPER_DOWNLOAD_ROOT=/models
ENV LOG_LEVEL=INFO

RUN adduser --disabled-password --gecos "" klai && \
    mkdir -p /models && chown klai:klai /models

EXPOSE 8000

USER klai

CMD ["python3", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
