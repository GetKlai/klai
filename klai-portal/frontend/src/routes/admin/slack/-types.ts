// SPEC-SLACK-001 §7.1 - portal admin API shapes for /api/admin/slack.

export type SlackStatus = 'not_connected' | 'awaiting_link' | 'connected' | 'revoked'

export interface SlackKey {
  id: string
  key_prefix: string
  rate_limit_rpm: number
  last_used_at: string | null
  created_at: string
}

export interface SlackInstallation {
  team_id: string
  team_name: string
  installed_by_slack_user_id: string
  installed_at: string
  revoked_at: string | null
}

export interface SlackStatusResponse {
  status: SlackStatus
  key: SlackKey | null
  installation: SlackInstallation | null
  kb_ids: number[]
}

export interface CreateSlackKeyRequest {
  kb_ids: number[]
}

export interface CreateSlackKeyResponse {
  api_key: string
  status: 'awaiting_link'
  key: SlackKey
}

export interface UpdateSlackKbsRequest {
  kb_ids: number[]
}

/** 204 resolves to undefined; 200 means klai-slack could not be reached. */
export type DisconnectSlackResponse = { slack_revoked: false } | undefined
