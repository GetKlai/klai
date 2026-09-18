// Shared refresh prompt for an optimistic-concurrency conflict (409) on a
// support case: the case or its analysis changed since the reviewer opened it,
// so their pending edit is kept and a refresh is offered rather than silently
// overwriting the newer state. Refreshing refetches the detail query the whole
// page reads from. Used by every reviewer mutation on the case (roles,
// finding review, reference, re-analysis).
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'

export function CaseStalePrompt({ caseId, message }: { caseId: string; message: string }) {
  const queryClient = useQueryClient()
  return (
    <div className="rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 p-2">
      <p className="text-xs text-[var(--color-warning-text)]">{message}</p>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="mt-2"
        onClick={() => void queryClient.invalidateQueries({ queryKey: ['support-case', caseId] })}
      >
        {m.support_case_review_refresh()}
      </Button>
    </div>
  )
}
