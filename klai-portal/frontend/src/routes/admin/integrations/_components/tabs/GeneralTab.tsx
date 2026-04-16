import { useState, useEffect } from 'react'
import { Loader2, Copy } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { IntegrationDetailResponse } from '../../-types'
import { useUpdateIntegration } from '../../-hooks'
import { EmbedSnippet } from '../EmbedSnippet'

interface GeneralTabProps {
  integration: IntegrationDetailResponse
}

export function GeneralTab({ integration }: GeneralTabProps) {
  const updateMutation = useUpdateIntegration(String(integration.id))

  const [name, setName] = useState(integration.name)
  const [description, setDescription] = useState(integration.description ?? '')

  // Sync state when integration refreshes
  useEffect(() => {
    setName(integration.name)
    setDescription(integration.description ?? '')
  }, [integration.name, integration.description])

  const isDisabled = integration.active === false || updateMutation.isPending
  const isWidget = integration.integration_type === 'widget'
  const isDirty =
    name.trim() !== integration.name ||
    (description.trim() || null) !== integration.description

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    updateMutation.mutate(
      {
        name: name.trim(),
        description: description.trim() || null,
      },
      {
        onSuccess: () => {
          toast.success(m.admin_integrations_success_updated())
        },
      },
    )
  }

  function handleCopyWidgetId() {
    if (!integration.widget_id) return
    void navigator.clipboard.writeText(integration.widget_id)
    toast.success(m.admin_integrations_widget_embed_copied())
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="integration-name">
            {m.admin_integrations_field_name()}
          </Label>
          <Input
            id="integration-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            disabled={isDisabled}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="integration-description">
            {m.admin_integrations_field_description()}
          </Label>
          <textarea
            id="integration-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            disabled={isDisabled}
            className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>

        {/* API: key prefix (readonly) */}
        {!isWidget && (
          <div className="space-y-1.5">
            <Label>{m.admin_integrations_col_key_prefix()}</Label>
            <code className="block text-xs font-mono text-[var(--color-muted-foreground)] py-2">
              {integration.key_prefix}...
            </code>
          </div>
        )}
      </section>

      {/* Widget: widget ID + embed snippet */}
      {isWidget && integration.widget_id && (
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_section_widget_embed()}
          </h2>
          <div className="space-y-2">
            <Label>{m.admin_integrations_widget_id_label()}</Label>
            <div className="flex items-center gap-2">
              <code className="block flex-1 text-xs font-mono text-[var(--color-muted-foreground)] bg-[var(--color-muted)] border border-[var(--color-border)] rounded-md px-3 py-2">
                {integration.widget_id}
              </code>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={handleCopyWidgetId}
                className="shrink-0"
              >
                <Copy className="h-4 w-4 mr-1.5" />
                {m.admin_integrations_widget_id_copy()}
              </Button>
            </div>
          </div>
          <EmbedSnippet
            widgetId={integration.widget_id}
            title={integration.widget_config?.title || undefined}
            welcomeMessage={integration.widget_config?.welcome_message || undefined}
          />
        </section>
      )}

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_integrations_error_generic()}
        </p>
      )}

      {integration.active && (
        <div className="pt-2">
          <Button
            type="submit"
            disabled={
              updateMutation.isPending || name.trim().length < 3 || !isDirty
            }
          >
            {updateMutation.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
            {m.admin_integrations_save()}
          </Button>
        </div>
      )}
    </form>
  )
}
