from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_shield_auth_rejects_partner_key_prefix():
    from app.api.shield import get_shield_auth

    request = SimpleNamespace(headers={"authorization": "Bearer pk_live_" + ("a" * 40)})
    with pytest.raises(HTTPException) as exc:
        await get_shield_auth(request, db=SimpleNamespace())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_shield_auth_requires_active_platform_admin_user():
    from app.api.shield import get_shield_auth
    from app.services.shield_tokens import generate_shield_token

    plaintext, token_hash = generate_shield_token()
    token = SimpleNamespace(id="token-1", org_id=101, user_id="uid-admin", token_hash=token_hash)

    class TokenResult:
        def scalar_one_or_none(self):
            return token

    class MissingPlatformAdminResult:
        def one_or_none(self):
            return None

    class FakeDb:
        def __init__(self):
            self.calls = 0

        async def execute(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return TokenResult()
            return MissingPlatformAdminResult()

    request = SimpleNamespace(headers={"authorization": f"Bearer {plaintext}"})
    with pytest.raises(HTTPException) as exc:
        await get_shield_auth(request, db=FakeDb())
    assert exc.value.status_code == 401
