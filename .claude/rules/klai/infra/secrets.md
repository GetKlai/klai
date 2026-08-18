---
paths:
  - "klai-infra/**/*.sops"
  - "klai-infra/.github/workflows/**/*.{yml,yaml}"
  - "klai-infra/docs/runbooks/**/*.md"
  - "deploy/**/*.{yml,yaml}"
  - "klai-*/**/config.py"
  - "klai-*/**/auth.py"
  - "klai-*/**/middleware/auth.py"
  - "klai-*/**/main.py"
---
# Secrets and fail-closed configuration

## SOPS is the durable source

Edit the encrypted inventory, never the generated live `.env`. Follow `klai-infra/docs/runbooks/sops-edit-via-core01.md` when a local age key is unavailable.

After every edit, decrypt the new ciphertext again and compare the plaintext line count and intended key set with the edited plaintext. Refuse the change on unexplained drift; SOPS dotenv roundtrips can normalize blank lines. Keep plaintext only for the edit and remove it immediately afterwards.

The `sync-env.yml` workflow refuses key removal by default. Intentional removal requires a manual dispatch with the exact typed value `allow_removal=I-CONFIRM-REMOVAL`. Preserve unexpected live-only keys in SOPS instead of bypassing the guard.

## Validators land after configuration

Tests may inject a secret that production lacks. Before adding a startup validator or removing a fallback:

1. locate the variable in the owning SOPS file;
2. locate its compose mapping;
3. deploy and verify the environment first;
4. then land the validator and dependent code.

An absent mandatory secret must fail at startup with a clear configuration error, not on the first request.

For encoded cryptographic keys, validate decoding and the algorithm's exact
byte length at configuration load, not only non-emptiness. Connector's
`encryption_key` validator and `tests/test_encryption_key_validator.py` are the
current AES-256 example.

## Shared-secret authentication

- Reject both a missing configured secret and a missing supplied secret before comparison. Empty equals empty must never authenticate.
- Compare shared secrets and signatures with `hmac.compare_digest`, not `==` or `!=`.
- Keep authentication failures fail-closed. Do not catch them and continue with anonymous, empty-context, or unrestricted behavior.
- Test missing, empty, wrong, and correct values. Include a source-level assertion or security rule where a regression to ordinary comparison would otherwise be easy.

Current implementations include retrieval-api's auth middleware, scribe's auth helper, mailer's internal-send auth, connector's auth middleware, and knowledge-ingest webhook/auth paths. Verify the exact owning implementation before copying a pattern.
