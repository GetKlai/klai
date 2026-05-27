-- SPEC-VOYS-HUBSPOT-HANDOFF-001
-- Store the best-effort HubSpot agent display name for visitor UI and reload persistence.

ALTER TABLE widget_handoff_messages
  ADD COLUMN IF NOT EXISTS agent_name text;
