-- REQ-8 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 Finding B-5, HIGH):
-- Add CHECK constraint capping widget_messages.content at 10000 characters.
--
-- Run as the 'klai' superuser (portal_api cannot ALTER klai-owned tables).
-- Apply after: alembic upgrade head stamps revision 57b2c33efe55.
--
-- This is a belt-and-suspenders constraint — the application already clamps
-- content to 10000 chars before INSERT (AC8.1). The DB constraint prevents
-- any future code path from silently bypassing the cap.

ALTER TABLE widget_messages
    ADD CONSTRAINT widget_messages_content_length
    CHECK (LENGTH(content) <= 10000);
