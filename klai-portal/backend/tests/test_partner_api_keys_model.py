"""RED: Verify PartnerAPIKey + PartnerApiKeyKbAccess model structure.

SPEC-API-001 REQ-1.2, REQ-1.3:
- Table names, column types, PKs, nullability, defaults, FK targets, indexes.
SPEC-WIDGET-001 Task 1:
- integration_type, widget_id, widget_config columns and helpers.
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

    # id: UUID PK
    assert "id" in columns
    col = columns["id"]
    assert isinstance(col.type, UUID)
    assert col.primary_key

    # org_id: Integer FK, not null
    assert "org_id" in columns
    col = columns["org_id"]
    assert not col.nullable

    # name: String(128), not null
    assert "name" in columns
    col = columns["name"]
    assert not col.nullable

    # description: String(512), nullable
    assert "description" in columns
    col = columns["description"]
    assert col.nullable

    # key_prefix: String(12), not null
    assert "key_prefix" in columns
    col = columns["key_prefix"]
    assert not col.nullable

    # key_hash: String(64), not null, unique
    assert "key_hash" in columns
    col = columns["key_hash"]
    assert not col.nullable
    assert col.unique

    # permissions: JSONB, not null
    assert "permissions" in columns
    col = columns["permissions"]
    assert isinstance(col.type, JSONB)
    assert not col.nullable

    # rate_limit_rpm: Integer, not null
    assert "rate_limit_rpm" in columns
    col = columns["rate_limit_rpm"]
    assert not col.nullable

    # active: Boolean, not null
    assert "active" in columns
    col = columns["active"]
    assert not col.nullable

    # last_used_at: DateTime, nullable
    assert "last_used_at" in columns
    col = columns["last_used_at"]
    assert col.nullable

    # created_at: DateTime, not null
    assert "created_at" in columns
    col = columns["created_at"]
    assert not col.nullable

    # created_by: String(64), not null
    assert "created_by" in columns
    col = columns["created_by"]
    assert not col.nullable


def test_partner_api_keys_foreign_keys():
    """REQ-1.2: org_id FK points to portal_orgs.id."""
    from app.models.partner_api_keys import PartnerAPIKey

    mapper = sa_inspect(PartnerAPIKey)
    columns = {c.key: c for c in mapper.columns}

    fks = list(columns["org_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "portal_orgs.id"


def test_partner_api_keys_indexes():
    """REQ-1.2: key_hash has unique index, org_id has index."""
    from app.models.partner_api_keys import PartnerAPIKey

    table = PartnerAPIKey.__table__
    index_columns = {}
    for idx in table.indexes:
        cols = [c.name for c in idx.columns]
        index_columns[idx.name] = (cols, idx.unique)

    # key_hash unique index
    found_key_hash = False
    for _name, (cols, unique) in index_columns.items():
        if "key_hash" in cols and unique:
            found_key_hash = True
    assert found_key_hash, f"No unique index on key_hash. Indexes: {index_columns}"

    # org_id index
    found_org_id = False
    for _name, (cols, _unique) in index_columns.items():
        if "org_id" in cols:
            found_org_id = True
    assert found_org_id, f"No index on org_id. Indexes: {index_columns}"


def test_partner_api_key_kb_access_table_exists():
    """The PartnerApiKeyKbAccess model maps to 'partner_api_key_kb_access' table."""
    from app.models.partner_api_keys import PartnerApiKeyKbAccess

    assert PartnerApiKeyKbAccess.__tablename__ == "partner_api_key_kb_access"


def test_partner_api_key_kb_access_composite_pk():
    """REQ-1.3: Composite PK on (partner_api_key_id, kb_id)."""
    from app.models.partner_api_keys import PartnerApiKeyKbAccess

    mapper = sa_inspect(PartnerApiKeyKbAccess)
    pk_cols = [c.key for c in mapper.columns if c.primary_key]
    assert set(pk_cols) == {"partner_api_key_id", "kb_id"}


def test_partner_api_key_kb_access_columns():
    """REQ-1.3: All required columns with correct types."""
    from app.models.partner_api_keys import PartnerApiKeyKbAccess

    mapper = sa_inspect(PartnerApiKeyKbAccess)
    columns = {c.key: c for c in mapper.columns}

    # partner_api_key_id: UUID FK
    assert "partner_api_key_id" in columns
    fks = list(columns["partner_api_key_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "partner_api_keys.id"

    # kb_id: Integer FK
    assert "kb_id" in columns
    fks = list(columns["kb_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "portal_knowledge_bases.id"

    # access_level: String(16), not null
    assert "access_level" in columns
    col = columns["access_level"]
    assert not col.nullable


def test_partner_api_keys_inherits_from_base():
    """Both models inherit from the project Base."""
    from app.models.base import Base
    from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess

    assert issubclass(PartnerAPIKey, Base)
    assert issubclass(PartnerApiKeyKbAccess, Base)


# ---------------------------------------------------------------------------
# SPEC-WIDGET-001 Task 1: New columns on PartnerAPIKey
# ---------------------------------------------------------------------------


def test_partner_api_key_has_integration_type_column():
    """SPEC-WIDGET-001: integration_type column exists, String, not null."""
    from app.models.partner_api_keys import PartnerAPIKey

    mapper = sa_inspect(PartnerAPIKey)
    columns = {c.key: c for c in mapper.columns}

    assert "integration_type" in columns, "integration_type column missing from PartnerAPIKey"
    col = columns["integration_type"]
    assert not col.nullable, "integration_type must be NOT NULL"


def test_partner_api_key_has_widget_id_column():
    """SPEC-WIDGET-001: widget_id column exists, nullable, unique."""
    from app.models.partner_api_keys import PartnerAPIKey

    mapper = sa_inspect(PartnerAPIKey)
    columns = {c.key: c for c in mapper.columns}

    assert "widget_id" in columns, "widget_id column missing from PartnerAPIKey"
    col = columns["widget_id"]
    assert col.nullable, "widget_id must be nullable (only set for widget integration type)"
    assert col.unique, "widget_id must be unique"


def test_partner_api_key_has_widget_config_column():
    """SPEC-WIDGET-001: widget_config column exists as JSONB, nullable."""
    from app.models.partner_api_keys import PartnerAPIKey

    mapper = sa_inspect(PartnerAPIKey)
    columns = {c.key: c for c in mapper.columns}

    assert "widget_config" in columns, "widget_config column missing from PartnerAPIKey"
    col = columns["widget_config"]
    assert isinstance(col.type, JSONB), "widget_config must be JSONB type"
    assert col.nullable, "widget_config must be nullable"


def test_integration_type_default_value():
    """SPEC-WIDGET-001: integration_type defaults to 'api'."""
    from app.models.partner_api_keys import PartnerAPIKey

    mapper = sa_inspect(PartnerAPIKey)
    columns = {c.key: c for c in mapper.columns}
    col = columns["integration_type"]

    # server_default is a text clause; check it contains 'api'
    assert col.server_default is not None, "integration_type must have a server_default of 'api'"
    server_default_str = str(col.server_default.arg)
    assert "api" in server_default_str, f"server_default should be 'api', got: {server_default_str}"


# ---------------------------------------------------------------------------
# SPEC-WIDGET-001: widget_id generator helper
# ---------------------------------------------------------------------------


def test_generate_widget_id_format():
    """SPEC-WIDGET-001: generate_widget_id() returns wgt_ + 40 hex chars."""
    from app.models.partner_api_keys import generate_widget_id

    widget_id = generate_widget_id()

    assert widget_id.startswith("wgt_"), f"widget_id must start with 'wgt_', got: {widget_id}"
    suffix = widget_id[len("wgt_") :]
    assert len(suffix) == 40, f"suffix must be 40 chars, got {len(suffix)}: {suffix}"
    assert suffix == suffix.lower(), "suffix must be lowercase"
    int(suffix, 16)  # raises ValueError if not valid hex


def test_generate_widget_id_unique():
    """SPEC-WIDGET-001: generate_widget_id() produces different IDs each call."""
    from app.models.partner_api_keys import generate_widget_id

    ids = {generate_widget_id() for _ in range(10)}
    assert len(ids) == 10, "generate_widget_id() must return unique IDs each call"


def test_generate_widget_id_length():
    """SPEC-WIDGET-001: total length is 44 chars (4 prefix + 40 hex)."""
    from app.models.partner_api_keys import generate_widget_id

    widget_id = generate_widget_id()
    assert len(widget_id) == 44, f"Total length must be 44, got {len(widget_id)}"
