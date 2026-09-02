import { Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { PreviewResult } from '../-connector-types'

type AiSelectorStepProps = {
  result: PreviewResult
  currentSelector: string
  canTryAi: boolean
  isPending: boolean
  onUseSelector: (selector: string) => void
  onTryAi: () => void
}

export function AiSelectorStep({
  result,
  currentSelector,
  canTryAi,
  isPending,
  onUseSelector,
  onTryAi,
}: AiSelectorStepProps) {
  const aiSelector = result.selector_source === 'ai' && result.content_selector
    ? result.content_selector
    : null
  const canOfferAi =
    canTryAi &&
    (result.classification === 'selector_required' || result.classification === 'selector_returns_empty') &&
    result.selector_source !== 'ai' &&
    result.selector_source !== 'ai_failed'

  return (
    <>
      {aiSelector !== null && (
        <div className="rounded-lg border border-gray-200 bg-black/[0.06] p-3 space-y-2">
          <div className="flex gap-2 items-center text-xs text-gray-600">
            <Sparkles className="h-3.5 w-3.5 shrink-0" />
            <span>
              {m.admin_connectors_webcrawler_ai_selector_detected({
                selector: aiSelector,
                count: String(result.word_count),
              })}
            </span>
          </div>
          {currentSelector !== aiSelector && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="text-xs h-7"
              onClick={() => onUseSelector(aiSelector)}
            >
              {m.admin_connectors_webcrawler_ai_selector_use()}
            </Button>
          )}
        </div>
      )}

      {canOfferAi && (
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900 transition-colors disabled:opacity-50"
          disabled={isPending}
          onClick={onTryAi}
        >
          <Sparkles className="h-3 w-3" />
          {m.admin_connectors_webcrawler_try_ai()}
        </button>
      )}
    </>
  )
}
