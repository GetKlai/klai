# klai-service-auth

Receiver-side claim parsing for Klai inter-service authentication
(SPEC-SEC-SERVICE-AUTH-001 / -002).

A service that calls another internal service presents a Zitadel access token;
the receiver validates the JWT signature against Zitadel JWKS and reads the
granted scopes with `project_role_scopes`:

```python
from klai_service_auth import project_role_scopes

scopes = project_role_scopes(payload, settings.zitadel_api_audience)
```

Zitadel cannot populate the standard `scope` claim of a machine token, so the
scopes are the keys of the token's `urn:zitadel:iam:org:project:<projectId>:roles`
claim, pinned to the receiver's own project. The only runtime importer is
retrieval-api (`retrieval_api/middleware/auth.py`).

## Operator tool

`scripts/zitadel-create-service-account.py` creates (idempotently) the Zitadel
service account for an internal service. See its docstring for the environment
it needs and for where the minted client secret must go.
