"""
Tests for PortalOrgAllowedDomain model and domain validation utilities (SPEC-AUTH-006 R2-R3).
"""


class TestPortalOrgAllowedDomainModel:
    """Verify the SQLAlchemy model is correctly defined."""

    def test_model_has_expected_columns(self) -> None:
        from app.models.portal import PortalOrgAllowedDomain

        mapper = PortalOrgAllowedDomain.__table__
        column_names = {c.name for c in mapper.columns}
        assert "id" in column_names
        assert "org_id" in column_names
        assert "domain" in column_names
        assert "created_at" in column_names
        assert "created_by" in column_names

    def test_model_has_unique_constraints(self) -> None:
        from app.models.portal import PortalOrgAllowedDomain

        table = PortalOrgAllowedDomain.__table__
        constraint_names = {c.name for c in table.constraints if hasattr(c, "name") and c.name}
        assert "uq_org_allowed_domains_org_domain" in constraint_names
        assert "uq_org_allowed_domains_domain_global" in constraint_names

    def test_domain_column_max_length(self) -> None:
        from app.models.portal import PortalOrgAllowedDomain

        domain_col = PortalOrgAllowedDomain.__table__.columns["domain"]
        assert domain_col.type.length == 253
