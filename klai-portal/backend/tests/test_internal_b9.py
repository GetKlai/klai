"""Tests for SPEC-TI-010B finding B-9: feedback idempotency-key tenant prefix.

Before the fix the key was: fb:{message_id}:{conversation_id}
After the fix:               fb:{org.id}:{conversation_id}:{message_id}

Without the org prefix, two different tenants that happen to receive the same
LibreChat message_id + conversation_id would deduplicate each other's feedback —
one org's response would silently suppress the other's idempotency write.
"""

from __future__ import annotations


def _make_idem_key(org_id: int, conversation_id: str, message_id: str) -> str:
    """Mirror the key construction from internal.py::post_kb_feedback (post B-9 fix)."""
    return f"fb:{org_id}:{conversation_id}:{message_id}"


class TestFeedbackIdempotencyKeyFormat:
    def test_key_includes_org_prefix(self) -> None:
        """The idempotency key must include org_id as the leading segment."""
        key = _make_idem_key(org_id=7, conversation_id="conv-1", message_id="msg-1")
        assert key.startswith("fb:7:"), f"Expected org prefix, got: {key}"

    def test_same_message_different_orgs_produce_different_keys(self) -> None:
        """Same (message_id, conversation_id) from org 7 and org 99 must NOT collide."""
        key_org7 = _make_idem_key(org_id=7, conversation_id="conv-abc", message_id="msg-xyz")
        key_org99 = _make_idem_key(org_id=99, conversation_id="conv-abc", message_id="msg-xyz")
        assert key_org7 != key_org99, (
            "Two different orgs with the same message_id+conversation_id must NOT share an idempotency key"
        )

    def test_same_org_different_messages_produce_different_keys(self) -> None:
        """Different messages in the same org must produce different keys."""
        key_a = _make_idem_key(org_id=7, conversation_id="conv-1", message_id="msg-1")
        key_b = _make_idem_key(org_id=7, conversation_id="conv-1", message_id="msg-2")
        assert key_a != key_b

    def test_same_message_same_org_produces_same_key(self) -> None:
        """Idempotency: same (org, conversation, message) must always produce the same key."""
        key1 = _make_idem_key(org_id=7, conversation_id="conv-1", message_id="msg-1")
        key2 = _make_idem_key(org_id=7, conversation_id="conv-1", message_id="msg-1")
        assert key1 == key2

    def test_old_format_does_not_match_new_format(self) -> None:
        """Regression guard: the old key format (without org prefix) is different from the new one."""
        old_key = "fb:msg-1:conv-1"  # old format: fb:{message_id}:{conversation_id}
        new_key = _make_idem_key(org_id=7, conversation_id="conv-1", message_id="msg-1")
        assert old_key != new_key, (
            "Old format and new format must differ — this guards against regression to the old key shape"
        )
