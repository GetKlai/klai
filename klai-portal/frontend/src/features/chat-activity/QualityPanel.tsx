import { Badge } from '@/components/ui/badge'
import { OUTCOME_BADGE_VARIANT } from './badgeVariant'
import { OUTCOME_LABEL } from './labels'
import type { ConversationQuality } from './types'

/**
 * REQ-3 (SPEC-CHAT-QUALITY-LOOP-001): judge verdict panel for a conversation
 * drawer. Renders only when a judgment exists — a 404 ("not judged yet") is
 * normal and shows nothing.
 */
export function QualityPanel({ quality }: { quality: ConversationQuality }) {
  return (
    <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
      <Badge variant={OUTCOME_BADGE_VARIANT[quality.outcome] ?? 'secondary'}>
        {(OUTCOME_LABEL[quality.outcome] ?? (() => quality.outcome))()}
      </Badge>
      {quality.reasoning && (
        <p className="mt-2 text-xs leading-5 text-gray-600">{quality.reasoning}</p>
      )}
      {quality.suggested_action && (
        <p className="mt-1.5 text-xs leading-5 text-gray-700">{quality.suggested_action}</p>
      )}
    </div>
  )
}
