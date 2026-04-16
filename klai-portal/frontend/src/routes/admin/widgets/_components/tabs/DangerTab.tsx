import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Loader2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse } from '../../-types'
import { useDeleteWidget } from '../../-hooks'

interface Props {
  widget: WidgetDetailResponse
}

export function DangerTab({ widget }: Props) {
  const navigate = useNavigate()
  const deleteMutation = useDeleteWidget()
  const [confirming, setConfirming] = useState(false)

  function handleDelete() {
    deleteMutation.mutate(String(widget.id), {
      onSuccess: () => {
        toast.success(m.admin_widgets_delete_success())
        void navigate({ to: '/admin/widgets' })
      },
    })
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-medium text-[var(--color-destructive)] mb-2">
          {m.admin_widgets_delete_section_title()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)] mb-4">
          {m.admin_widgets_delete_section_description()}
        </p>
        <InlineDeleteConfirm
          isConfirming={confirming}
          isPending={deleteMutation.isPending}
          label={m.admin_widgets_delete_confirm({ name: widget.name })}
          cancelLabel={m.admin_users_cancel()}
          onConfirm={handleDelete}
          onCancel={() => setConfirming(false)}
        >
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={() => setConfirming(true)}
            disabled={deleteMutation.isPending}
          >
            {deleteMutation.isPending ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Trash2 className="h-4 w-4 mr-2" />
            )}
            {m.admin_widgets_delete_button()}
          </Button>
        </InlineDeleteConfirm>
      </div>
    </div>
  )
}
