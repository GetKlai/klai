import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { decisionLabel, type ClusterDecision } from '../-cluster-types'

interface Decided {
  decision: ClusterDecision | 'none'
  decided_by: string | null
  decided_at: string | null
}

/** "<decision> door <name> op <date>" for a final decision; nothing for an open cluster. */
export function DecisionLine({ decision }: { decision: Decided }) {
  if (decision.decision === 'none') return null
  const label = decisionLabel(decision.decision)
  const date = decision.decided_at ? new Date(decision.decided_at).toLocaleDateString(getLocale()) : ''
  return (
    <p className="text-sm text-gray-900">
      {decision.decided_by
        ? m.gaps_decision_line({ decision: label, who: decision.decided_by, date })
        : m.gaps_decision_line_no_actor({ decision: label, date })}
    </p>
  )
}
