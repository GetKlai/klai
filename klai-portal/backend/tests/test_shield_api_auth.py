from types import SimpleNamespace
import urllib.parse
import zipfile

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


def test_build_extension_zip_contains_manifest_and_source(tmp_path):
    from app.api.shield import _build_extension_zip_bytes

    extension_dir = tmp_path / "shield-extension"
    source_dir = extension_dir / "src" / "background"
    source_dir.mkdir(parents=True)
    (extension_dir / "manifest.json").write_text('{"manifest_version":3}', encoding="utf-8")
    (source_dir / "service-worker.js").write_text("console.log('ok')", encoding="utf-8")

    zip_bytes = _build_extension_zip_bytes(extension_dir)
    zip_path = tmp_path / "extension.zip"
    zip_path.write_bytes(zip_bytes)

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())

    assert "klai-shield-extension/manifest.json" in names
    assert "klai-shield-extension/src/background/service-worker.js" in names


def test_extension_login_return_to_hides_absolute_chrome_redirect():
    from app.api.shield import _extension_login_return_to

    request = SimpleNamespace(url=SimpleNamespace(path="/api/app/shield/extension/login"))
    return_to = _extension_login_return_to(request, "https://abc123.chromiumapp.org/shield")

    assert return_to.startswith("/api/app/shield/extension/login?redirect_uri_b64=")
    assert "://" not in urllib.parse.unquote(return_to)
