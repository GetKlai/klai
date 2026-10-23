"""Start for retrieval-api-oneoff.sh: the service as it runs, read-only against the live stores.

retrieval-api's own Postgres connections open read-only, and the two writes a /retrieve call makes
(the knowledge.queried product event and the telemetry shadow row) are skipped, so a measurement
leaves no trace in a tenant's counts. Redis (rate limit, caches) is used as the live service uses it.
"""

import os

import asyncpg
import uvicorn

_create_pool = asyncpg.create_pool


def _read_only_pool(*args, **kwargs):
    kwargs["server_settings"] = {**kwargs.get("server_settings", {}), "default_transaction_read_only": "on"}
    return _create_pool(*args, **kwargs)


asyncpg.create_pool = _read_only_pool

import retrieval_api.api.retrieve as retrieve  # noqa: E402  (after the pool patch: nothing may connect before it)
import retrieval_api.main as main  # noqa: E402


def _skip(*_args, **_kwargs):
    return None


retrieve.emit_event = _skip
retrieve.write_shadow = _skip

uvicorn.run(main.app, host="0.0.0.0", port=int(os.environ.get("PORT", "8040")))  # noqa: S104 - container-internal
