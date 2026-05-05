from __future__ import annotations

import asyncio

import fakeredis.aioredis
import pytest

from webhook_replay import NonceReplayError, RedisUnavailableError, WebhookNonceStore


def _store(
    client=None,
    *,
    prefix="test:nonce:",
    ttl_seconds=300,
    redis_url="redis://redis-test.local:6379/0",
):
    s = WebhookNonceStore(redis_url=redis_url, prefix=prefix, ttl_seconds=ttl_seconds)
    if client is not None:
        s.set_client(client)
    return s


async def _fake_client():
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


class TestAccept:
    async def test_new_single_part_nonce_accepted(self):
        client = await _fake_client()
        store = _store(client)
        await store.check_and_record("delivery-abc123")

    async def test_new_two_part_nonce_accepted(self):
        client = await _fake_client()
        store = _store(client)
        await store.check_and_record("1730890000", "abc123")

    async def test_new_three_part_nonce_accepted(self):
        client = await _fake_client()
        store = _store(client)
        await store.check_and_record("2026", "05", "abc")

    async def test_different_parts_produce_independent_slots(self):
        client = await _fake_client()
        store = _store(client)
        await store.check_and_record("1730890001", "aaaa")
        await store.check_and_record("1730890001", "bbbb")


class TestReplay:
    async def test_identical_parts_second_call_raises(self):
        client = await _fake_client()
        store = _store(client)
        await store.check_and_record("1730890000", "abc123")
        with pytest.raises(NonceReplayError):
            await store.check_and_record("1730890000", "abc123")

    async def test_replay_error_message_contains_key(self):
        client = await _fake_client()
        store = _store(client, prefix="svc:nonce:")
        await store.check_and_record("ts", "v1")
        with pytest.raises(NonceReplayError) as exc_info:
            await store.check_and_record("ts", "v1")
        assert "svc:nonce:" in str(exc_info.value)

    async def test_single_part_replay_rejected(self):
        client = await _fake_client()
        store = _store(client)
        await store.check_and_record("delivery-xyz")
        with pytest.raises(NonceReplayError):
            await store.check_and_record("delivery-xyz")


class _BrokenRedis:
    async def set(self, *args, **kwargs):
        raise ConnectionError("fake redis unavailable")


class TestRedisUnavailable:
    async def test_broken_redis_raises_unavailable(self):
        store = _store(_BrokenRedis())
        with pytest.raises(RedisUnavailableError):
            await store.check_and_record("ts", "v1")

    async def test_invalid_redis_url_raises_unavailable(self):
        store = WebhookNonceStore(redis_url="not-a-url", prefix="test:nonce:", ttl_seconds=300)
        with pytest.raises(RedisUnavailableError):
            await store.check_and_record("ts", "v1")

    async def test_unsupported_scheme_raises_unavailable(self):
        store = WebhookNonceStore(
            redis_url="memcached://host:11211", prefix="test:nonce:", ttl_seconds=300
        )
        with pytest.raises(RedisUnavailableError):
            await store.check_and_record("ts", "v1")


class TestTTL:
    async def test_nonce_accepted_after_ttl_expires(self):
        client = await _fake_client()
        store = _store(client, ttl_seconds=1)
        await store.check_and_record("ts", "v1")
        await asyncio.sleep(1.1)
        await store.check_and_record("ts", "v1")


class TestHooks:
    async def test_set_client_injects_client(self):
        real_client = await _fake_client()
        store = WebhookNonceStore(
            redis_url="redis://unreachable-host:6379/0",
            prefix="test:nonce:",
            ttl_seconds=300,
        )
        store.set_client(real_client)
        await store.check_and_record("ts", "v1")

    async def test_reset_client_is_safe_on_pristine_store(self):
        store = WebhookNonceStore(
            redis_url="redis://localhost:6379/0", prefix="test:nonce:", ttl_seconds=300
        )
        store.reset_client()

    async def test_reset_client_clears_injected_client(self):
        client = await _fake_client()
        store = _store(client)
        await store.check_and_record("ts", "v1")
        store.reset_client()
        assert store._client is None


class TestKeyFormat:
    async def test_key_uses_prefix_and_colon_joiner(self):
        client = await _fake_client()
        store = _store(client, prefix="svc:nonce:")
        await store.check_and_record("123", "abc")
        keys = await client.keys("*")
        assert b"svc:nonce:123:abc" in keys

    async def test_single_part_key_has_no_trailing_colon(self):
        client = await _fake_client()
        store = _store(client, prefix="svc:nonce:")
        await store.check_and_record("delivery-xyz")
        keys = await client.keys("*")
        assert b"svc:nonce:delivery-xyz" in keys


class TestPrefixIsolation:
    async def test_same_parts_different_prefix_are_independent(self):
        client = await _fake_client()
        store_a = _store(client, prefix="svc-a:nonce:")
        store_b = _store(client, prefix="svc-b:nonce:")
        await store_a.check_and_record("ts", "v1")
        await store_b.check_and_record("ts", "v1")
