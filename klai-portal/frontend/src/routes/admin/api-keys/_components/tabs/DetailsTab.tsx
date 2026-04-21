import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { ApiKeyDetailResponse } from '../../-types'
import { useUpdateApiKey } from '../../-hooks'

interface Props {
  apiKey: ApiKeyDetailResponse
}

export function DetailsTab({ apiKey }: Props) {
  const updateMutation = useUpdateApiKey(String(apiKey.id))
  const [name, setName] = useState(apiKey.name)
  const [description, setDescription] = useState(apiKey.description ?? '')

  useEffect(() => {
    setName(apiKey.name)
    setDescription(apiKey.description ?? '')
  }, [apiKey.name, apiKey.description])

  const isDirty =
    name.trim() !== apiKey.name ||
    (description.trim() || null) !== apiKey.description

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
          <Label htmlFor="api-key-name">{m.admin_shared_field_name()}</Label>
          <Input
            id="api-key-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="api-key-description">{m.admin_shared_field_description()}</Label>
          <textarea
            id="api-key-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)]"
          />
        </div>
        <div className="space-y-1.5">
          <Label>{m.admin_api_keys_col_key_prefix()}</Label>
          <code className="block text-xs font-mono text-[var(--color-muted-foreground)] py-2">
            {apiKey.key_prefix}...
          </code>
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
