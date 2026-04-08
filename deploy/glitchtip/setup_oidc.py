"""
GlitchTip OIDC setup — configures Zitadel as the OpenID Connect provider.

Run via:  docker compose run --rm glitchtip-oidc-setup
Idempotent: safe to re-run after credentials rotate or on a fresh DB.

What it does:
- Creates (or updates) a django-allauth SocialApp for Zitadel OIDC
- Attaches it to the default Django site
- Prints the callback URL to register in Zitadel
"""
import os
import sys
import django

django.setup()

from allauth.socialaccount.models import SocialApp  # noqa: E402 (after setup)
from django.contrib.sites.models import Site  # noqa: E402

PROVIDER_ID = "zitadel"
CLIENT_ID = os.environ.get("GLITCHTIP_OIDC_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GLITCHTIP_OIDC_CLIENT_SECRET", "").strip()
SERVER_URL = os.environ.get("GLITCHTIP_OIDC_SERVER_URL", "").strip()

# Validate
missing = [k for k, v in {
    "GLITCHTIP_OIDC_CLIENT_ID": CLIENT_ID,
    "GLITCHTIP_OIDC_CLIENT_SECRET": CLIENT_SECRET,
    "GLITCHTIP_OIDC_SERVER_URL": SERVER_URL,
}.items() if not v]

if missing:
    print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
    sys.exit(1)

# Create or update the SocialApp
app, created = SocialApp.objects.update_or_create(
    provider="openid_connect",
    provider_id=PROVIDER_ID,
    defaults={
        "name": "Klai (Zitadel)",
        "client_id": CLIENT_ID,
        "secret": CLIENT_SECRET,
        "settings": {
            "server_url": SERVER_URL,
            "token_auth_method": "client_secret_basic",
            "scope": ["openid", "email", "profile"],
        },
    },
)

# Attach to the current site (required by django-allauth)
site = Site.objects.get_current()
app.sites.add(site)

action = "Created" if created else "Updated"
domain = os.environ.get("GLITCHTIP_DOMAIN", "https://errors.example.com")
callback_url = f"{domain}/accounts/oidc/{PROVIDER_ID}/login/callback/"

print(f"  {action} OIDC social app: {app.name}")
print(f"  Server URL:   {SERVER_URL}")
print(f"  Client ID:    {CLIENT_ID}")
print(f"  Callback URL: {callback_url}")
print()
print("Register this callback URL in Zitadel → GlitchTip app → Redirect URIs")
