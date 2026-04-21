"""
Tests for PortalJoinRequest model and HMAC token generation (SPEC-AUTH-006 R5).
"""


class TestPortalJoinRequestModel:
    """Verify the SQLAlchemy model is correctly defined."""

    def test_model_has_expected_columns(self) -> None:
        from app.models.portal import PortalJoinRequest

        mapper = PortalJoinRequest.__table__
        column_names = {c.name for c in mapper.columns}
        expected = {
            "id",
            "zitadel_user_id",
            "email",
            "display_name",
            "org_id",
            "status",
            "requested_at",
            "reviewed_at",
            "reviewed_by",
            "approval_token",
            "expires_at",
        }
        assert expected.issubset(column_names)

    def test_approval_token_is_unique(self) -> None:
        from app.models.portal import PortalJoinRequest

        table = PortalJoinRequest.__table__
        approval_col = table.columns["approval_token"]
        assert approval_col.unique is True


class TestApprovalTokenGeneration:
    """Approval token must be HMAC-SHA256 of (id + zitadel_user_id)."""

    def test_generates_deterministic_token(self) -> None:
        from app.services.join_request_token import generate_approval_token

        token1 = generate_approval_token(1, "user-abc")
        token2 = generate_approval_token(1, "user-abc")
        assert token1 == token2

    def test_different_inputs_give_different_tokens(self) -> None:
        from app.services.join_request_token import generate_approval_token

        token1 = generate_approval_token(1, "user-abc")
        token2 = generate_approval_token(2, "user-abc")
        assert token1 != token2

    def test_token_is_hex_string(self) -> None:
        from app.services.join_request_token import generate_approval_token

        token = generate_approval_token(1, "user-abc")
        # HMAC-SHA256 produces 64 hex chars
        assert len(token) == 64
        int(token, 16)  # should not raise

    def test_validates_correct_token(self) -> None:
        from app.services.join_request_token import generate_approval_token, verify_approval_token

        token = generate_approval_token(42, "user-xyz")
        assert verify_approval_token(token, 42, "user-xyz") is True

    def test_rejects_wrong_token(self) -> None:
        from app.services.join_request_token import verify_approval_token

        assert verify_approval_token("deadbeef" * 8, 42, "user-xyz") is False
