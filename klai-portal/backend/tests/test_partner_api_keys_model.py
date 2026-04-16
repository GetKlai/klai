"""Verify PartnerAPIKey + PartnerApiKeyKbAccess model structure.

SPEC-API-001 REQ-1.2, REQ-1.3:
- Table names, column types, PKs, nullability, defaults, FK targets, indexes.

SPEC-WIDGET-002 removed columns integration_type, widget_id, widget_config
and active from partner_api_keys — those fields are no longer tested here.
"""

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import JSONB, UUID


def test_partner_api_keys_table_exists():
    """The PartnerAPIKey model maps to 'partner_api_keys' table."""
    from app.models.partner_api_keys import PartnerAPIKey

    assert PartnerAPIKey.__tablename__ == "partner_api_keys"


def test_partner_api_keys_columns():
    """REQ-1.2: All required columns exist with correct types and nullability."""
    from app.models.partner_api_keys import PartnerAPIKey

    mapper = sa_inspect(PartnerAPIKey)
    columns = {c.key: c for c in mapper.columns}

    # Required columns
    assert "id" in columns
    assert isinstance(columns["id"].type, UUID)
    assert columns["id"].primary_key

    assert "org_id" in columns
    assert not columns["org_id"].nullable

    assert "name" in columns
    assert not columns["name"].nullable

    assert "description" in columns
    assert columns["description"].nullable

    assert "key_prefix" in columns
    assert not columns["key_prefix"].nullable

    assert "key_hash" in columns
    assert not columns["key_hash"].nullable

    assert "permissions" in columns
    assert isinstance(columns["permissions"].type, JSONB)

    assert "rate_limit_rpm" in columns
    assert "last_used_at" in columns
    assert columns["last_used_at"].nullable

    assert "created_at" in columns
    assert not columns["created_at"].nullable

    assert "created_by" in columns


def test_partner_api_keys_no_removed_columns():
    """SPEC-WIDGET-002: widget + active columns were removed."""
    from app.models.partner_api_keys import PartnerAPIKey

    mapper = sa_inspect(PartnerAPIKey)
    column_names = {c.key for c in mapper.columns}

    # These were removed in SPEC-WIDGET-002
    assert "integration_type" not in column_names
    assert "widget_id" not in column_names
    assert "widget_config" not in column_names
    assert "active" not in column_names


def test_partner_api_key_kb_access_table_exists():
    """REQ-1.3: The junction table model maps correctly."""
    from app.models.partner_api_keys import PartnerApiKeyKbAccess

    assert PartnerApiKeyKbAccess.__tablename__ == "partner_api_key_kb_access"


def test_partner_api_key_kb_access_columns():
    """REQ-1.3: Junction has partner_api_key_id, kb_id (composite PK), access_level."""
    from app.models.partner_api_keys import PartnerApiKeyKbAccess

    mapper = sa_inspect(PartnerApiKeyKbAccess)
    columns = {c.key: c for c in mapper.columns}

    assert "partner_api_key_id" in columns
    assert columns["partner_api_key_id"].primary_key
    assert isinstance(columns["partner_api_key_id"].type, UUID)

    assert "kb_id" in columns
    assert columns["kb_id"].primary_key

    assert "access_level" in columns
    assert not columns["access_level"].nullable


def test_generate_partner_key():
    """The key generator yields a pk_live_ prefixed secret."""
    from app.services.partner_keys import generate_partner_key

    plaintext, key_hash = generate_partner_key()
    assert plaintext.startswith("pk_live_")
    assert len(key_hash) == 64  # SHA-256 hex
