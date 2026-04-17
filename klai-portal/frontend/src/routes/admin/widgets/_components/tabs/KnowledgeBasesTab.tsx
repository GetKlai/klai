import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse } from '../../-types'
import { useUpdateWidget } from '../../-hooks'
import { KbAccessEditor } from '../KbAccessEditor'

interface Props {
  widget: WidgetDetailResponse
}

export function KnowledgeBasesTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const [kbIds, setKbIds] = useState<number[]>(widget.kb_access.map((ka) => ka.kb_id))

  useEffect(() => {
    setKbIds(widget.kb_access.map((ka) => ka.kb_id))
  }, [widget.kb_access])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    updateMutation.mutate(
      { kb_ids: kbIds },
      {
        onSuccess: () => toast.success(m.admin_shared_success_updated()),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.admin_widgets_wizard_kb_access_intro_widget()}
        </p>
        <KbAccessEditor value={kbIds} onChange={setKbIds} />
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_shared_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button type="submit" disabled={updateMutation.isPending}>
          {updateMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          {m.admin_shared_save()}
        </Button>
      </div>
    </form>
  )
}
