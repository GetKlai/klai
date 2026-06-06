BEGIN;

ALTER TABLE platform_message_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_message_threads FORCE ROW LEVEL SECURITY;
ALTER TABLE platform_message_participants ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_message_participants FORCE ROW LEVEL SECURITY;
ALTER TABLE platform_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE platform_messages FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS platform_message_threads_select ON platform_message_threads;
CREATE POLICY platform_message_threads_select ON platform_message_threads
    FOR SELECT
    USING (
        current_setting('app.cross_org_admin', true) = 'true'
        OR EXISTS (
            SELECT 1
            FROM platform_message_participants p
            WHERE p.thread_id = platform_message_threads.id
              AND p.org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
              AND p.user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
        )
    );

DROP POLICY IF EXISTS platform_message_threads_insert ON platform_message_threads;
CREATE POLICY platform_message_threads_insert ON platform_message_threads
    FOR INSERT
    WITH CHECK (current_setting('app.cross_org_admin', true) = 'true');

DROP POLICY IF EXISTS platform_message_threads_update ON platform_message_threads;
CREATE POLICY platform_message_threads_update ON platform_message_threads
    FOR UPDATE
    USING (
        current_setting('app.cross_org_admin', true) = 'true'
        OR EXISTS (
            SELECT 1
            FROM platform_message_participants p
            WHERE p.thread_id = platform_message_threads.id
              AND p.org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
              AND p.user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
        )
    )
    WITH CHECK (
        current_setting('app.cross_org_admin', true) = 'true'
        OR EXISTS (
            SELECT 1
            FROM platform_message_participants p
            WHERE p.thread_id = platform_message_threads.id
              AND p.org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
              AND p.user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
        )
    );

DROP POLICY IF EXISTS platform_message_threads_delete ON platform_message_threads;
CREATE POLICY platform_message_threads_delete ON platform_message_threads
    FOR DELETE
    USING (current_setting('app.cross_org_admin', true) = 'true');

DROP POLICY IF EXISTS platform_message_participants_select ON platform_message_participants;
CREATE POLICY platform_message_participants_select ON platform_message_participants
    FOR SELECT
    USING (
        current_setting('app.cross_org_admin', true) = 'true'
        OR (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
            AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
        )
    );

DROP POLICY IF EXISTS platform_message_participants_insert ON platform_message_participants;
CREATE POLICY platform_message_participants_insert ON platform_message_participants
    FOR INSERT
    WITH CHECK (current_setting('app.cross_org_admin', true) = 'true');

DROP POLICY IF EXISTS platform_message_participants_update ON platform_message_participants;
CREATE POLICY platform_message_participants_update ON platform_message_participants
    FOR UPDATE
    USING (
        current_setting('app.cross_org_admin', true) = 'true'
        OR (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
            AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
        )
    )
    WITH CHECK (
        current_setting('app.cross_org_admin', true) = 'true'
        OR (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
            AND user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
        )
    );

DROP POLICY IF EXISTS platform_message_participants_delete ON platform_message_participants;
CREATE POLICY platform_message_participants_delete ON platform_message_participants
    FOR DELETE
    USING (current_setting('app.cross_org_admin', true) = 'true');

DROP POLICY IF EXISTS platform_messages_select ON platform_messages;
CREATE POLICY platform_messages_select ON platform_messages
    FOR SELECT
    USING (
        current_setting('app.cross_org_admin', true) = 'true'
        OR EXISTS (
            SELECT 1
            FROM platform_message_participants p
            WHERE p.thread_id = platform_messages.thread_id
              AND p.org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
              AND p.user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
        )
    );

DROP POLICY IF EXISTS platform_messages_insert ON platform_messages;
CREATE POLICY platform_messages_insert ON platform_messages
    FOR INSERT
    WITH CHECK (
        current_setting('app.cross_org_admin', true) = 'true'
        OR (
            org_id = NULLIF(current_setting('app.current_org_id', true), '')::integer
            AND sender_type = 'user'
            AND sender_user_id = NULLIF(current_setting('klai.changed_by_user_id', true), '')
            AND EXISTS (
                SELECT 1
                FROM platform_message_participants p
                WHERE p.thread_id = platform_messages.thread_id
                  AND p.org_id = platform_messages.org_id
                  AND p.user_id = platform_messages.sender_user_id
            )
        )
    );

DROP POLICY IF EXISTS platform_messages_update ON platform_messages;
CREATE POLICY platform_messages_update ON platform_messages
    FOR UPDATE
    USING (current_setting('app.cross_org_admin', true) = 'true')
    WITH CHECK (current_setting('app.cross_org_admin', true) = 'true');

DROP POLICY IF EXISTS platform_messages_delete ON platform_messages;
CREATE POLICY platform_messages_delete ON platform_messages
    FOR DELETE
    USING (current_setting('app.cross_org_admin', true) = 'true');

COMMIT;
