import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { OrgKbChecklist } from '@/components/admin/OrgKbChecklist'
import * as m from '@/paraglide/messages'
import { useUpdateSlackKbs } from '../-hooks'

interface Props {
  /** Server state; the parent remounts this tab (via `key`) when it changes. */
  kbIds: number[]
}

export function SlackKbTab({ kbIds }: Props) {
  const updateMutation = useUpdateSlackKbs()
  const [selected, setSelected] = useState(kbIds)

  const isDirty =
    selected.length !== kbIds.length || selected.some((id) => !kbIds.includes(id))

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    updateMutation.mutate(
      { kb_ids: selected },
      { onSuccess: () => toast.success(m.admin_shared_success_updated()) },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <p className="text-sm text-gray-600">{m.admin_slack_kbs_intro()}</p>
      <OrgKbChecklist value={selected} onChange={setSelected} disabled={updateMutation.isPending} />

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">{updateMutation.error.message}</p>
      )}

      <div className="flex items-center gap-3">
        <Button
          type="submit"
          disabled={updateMutation.isPending || selected.length === 0 || !isDirty}
        >
          {updateMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {m.admin_shared_save()}
        </Button>
        {selected.length === 0 && (
          <span className="text-xs text-gray-600">{m.admin_slack_kb_required()}</span>
        )}
      </div>
    </form>
  )
}
