import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse } from '../../-types'
import { useUpdateWidget } from '../../-hooks'

interface Props {
  widget: WidgetDetailResponse
}

export function DetailsTab({ widget }: Props) {
  const updateMutation = useUpdateWidget(String(widget.id))
  const [name, setName] = useState(widget.name)
  const [description, setDescription] = useState(widget.description ?? '')

  useEffect(() => {
    setName(widget.name)
    setDescription(widget.description ?? '')
  }, [widget.name, widget.description])

  const isDirty =
    name.trim() !== widget.name ||
    (description.trim() || null) !== widget.description

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    updateMutation.mutate(
      { name: name.trim(), description: description.trim() || null },
      {
        onSuccess: () => toast.success(m.admin_shared_success_updated()),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="widget-name">{m.admin_shared_field_name()}</Label>
          <Input
            id="widget-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="widget-description">{m.admin_shared_field_description()}</Label>
          <textarea
            id="widget-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)]"
          />
        </div>
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_shared_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button
          type="submit"
          disabled={updateMutation.isPending || name.trim().length < 3 || !isDirty}
        >
          {updateMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          {m.admin_shared_save()}
        </Button>
      </div>
    </form>
  )
}
