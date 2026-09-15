// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: single source of truth for the shapes the
// chat transcript and the nightly judge panel render. The widget drawer, the
// platform drawer and the knowledge activity screen all read these, so a new
// surface never copies the shape again.

export interface MessageSource {
  label: string
  title: string
  url: string
}

/** Per-message retrieval signals written by the answer pipeline. */
export interface ConversationAnswerSignals {
  top_score?: number | null
  band?: 'high' | 'medium' | 'low' | 'unknown'
  gap_type?: 'hard' | 'soft' | null
  sources_count?: number
  refused?: boolean
  broad_mode?: boolean
  language?: string | null
  model?: string | null
}

export interface ConversationMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  sources: MessageSource[] | null
  created_at: string
  sequence: number
  rating: 'thumbsUp' | 'thumbsDown' | null
  /** Not rendered yet; carried so the knowledge side can read it. */
  answer_signals?: ConversationAnswerSignals | null
}

/**
 * REQ-3 (SPEC-CHAT-QUALITY-LOOP-001): the nightly judge's verdict, read from
 * its own sidecar endpoint so the transcript types stay untouched.
 */
export interface ConversationQuality {
  outcome: string
  failure_category: string | null
  reasoning: string | null
  confidence: string | null
  suggested_action: string | null
  judged_at: string | null
  model_used?: string | null
}
