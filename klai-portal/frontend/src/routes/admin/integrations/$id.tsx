import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState, useEffect } from 'react'
import { ArrowLeft, Loader2, ExternalLink } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { useIntegration, useUpdateIntegration, useRevokeIntegration } from './-hooks'
import type { AccessLevel } from './-types'
import { KbAccessEditor } from './_components/KbAccessEditor'
import { RevokeConfirmDialog } from './_components/RevokeConfirmDialog'

export const Route = createFileRoute('/admin/integrations/$id')({
  component: IntegrationDetailPage,
})

interface FormState {
  name: string
  description: string
  chat: boolean
  feedback: boolean
  knowledge_append: boolean
  rate_limit_rpm: number
  kb_access: { kb_id: number; access_level: AccessLevel }[]
}

function IntegrationDetailPage() {
  const { id } = Route.useParams()
  const navigate = useNavigate()

  const { data: integration, isLoading, error, refetch } = useIntegration(id)
  const updateMutation = useUpdateIntegration(id)
  const revokeMutation = useRevokeIntegration(id)

  const [form, setForm] = useState<FormState | null>(null)
  const [showRevokeDialog, setShowRevokeDialog] = useState(false)

  // Populate form when integration data loads
  useEffect(() => {
    if (integration && !form) {
      setForm({
        name: integration.name,
        description: integration.description ?? '',
        chat: integration.permissions.chat,
        feedback: integration.permissions.feedback,
        knowledge_append: integration.permissions.knowledge_append,
        rate_limit_rpm: integration.rate_limit_rpm,
        kb_access: integration.kb_access.map((ka) => ({
          kb_id: ka.kb_id,
          access_level: ka.access_level,
        })),
      })
    }
  }, [integration, form])

  const isRevoked = integration?.active === false
  const isDisabled = isRevoked || updateMutation.isPending

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!form) return
    updateMutation.mutate(
      {
        name: form.name.trim(),
        description: form.description.trim() || null,
        permissions: {
          chat: form.chat,
          feedback: form.feedback,
          knowledge_append: form.knowledge_append,
        },
        rate_limit_rpm: form.rate_limit_rpm,
        kb_access: form.kb_access,
      },
      {
        onSuccess: () => {
          toast.success(m.admin_integrations_success_updated())
        },
      },
    )
  }

  function handleRevoke() {
    revokeMutation.mutate(undefined, {
      onSuccess: () => {
        setShowRevokeDialog(false)
        toast.success(m.admin_integrations_success_revoked())
        // Reset form to reflect revoked state
        setForm(null)
      },
    })
  }

  // When knowledge_append is toggled off, downgrade any read_write to read
  function handleKnowledgeAppendChange(checked: boolean) {
    if (!form) return
    setForm({
      ...form,
      knowledge_append: checked,
      kb_access: checked
        ? form.kb_access
        : form.kb_access.map((row) =>
            row.access_level === 'read_write'
              ? { ...row, access_level: 'read' as AccessLevel }
              : row,
          ),
    })
  }

  const grafanaUrl = `https://grafana.getklai.com/explore?left={"queries":[{"expr":"partner_key_id:${id}"}]}`

  if (isLoading) {
    return (
      <div className="p-6">
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_integrations_loading()}
        </p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 max-w-lg">
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }

  if (!integration || !form) return null

  return (
    <div className="p-6 max-w-lg">
      <div className="flex items-start justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {integration.name}
          </h1>
          {isRevoked ? (
            <Badge variant="destructive">
              {m.admin_integrations_status_revoked()}
            </Badge>
          ) : (
            <Badge variant="success">
              {m.admin_integrations_status_active()}
            </Badge>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/integrations' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_users_cancel()}
        </Button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Basics */}
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_section_basics()}
          </h2>
          <div className="space-y-1.5">
            <Label htmlFor="integration-name">
              {m.admin_integrations_field_name()}
            </Label>
            <Input
              id="integration-name"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
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
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              rows={3}
              disabled={isDisabled}
              className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
            />
          </div>

          {/* Key prefix (read-only) */}
          <div className="space-y-1.5">
            <Label>{m.admin_integrations_col_key_prefix()}</Label>
            <code className="block text-xs font-mono text-[var(--color-muted-foreground)] py-2">
              {integration.key_prefix}...
            </code>
          </div>
        </section>

        {/* Permissions */}
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_section_permissions()}
          </h2>
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-sm text-[var(--color-foreground)]">
              <input
                type="checkbox"
                checked={form.chat}
                onChange={(e) => setForm({ ...form, chat: e.target.checked })}
                disabled={isDisabled}
                className="accent-[var(--color-accent)]"
              />
              {m.admin_integrations_perm_chat()}
            </label>
            <label className="flex items-center gap-2 text-sm text-[var(--color-foreground)]">
              <input
                type="checkbox"
                checked={form.feedback}
                onChange={(e) => setForm({ ...form, feedback: e.target.checked })}
                disabled={isDisabled}
                className="accent-[var(--color-accent)]"
              />
              {m.admin_integrations_perm_feedback()}
            </label>
            <label className="flex items-center gap-2 text-sm text-[var(--color-foreground)]">
              <input
                type="checkbox"
                checked={form.knowledge_append}
                onChange={(e) => handleKnowledgeAppendChange(e.target.checked)}
                disabled={isDisabled}
                className="accent-[var(--color-accent)]"
              />
              {m.admin_integrations_perm_knowledge_append()}
            </label>
          </div>
        </section>

        {/* KB Access */}
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_section_kb_access()}
          </h2>
          <KbAccessEditor
            value={form.kb_access}
            onChange={(kb_access) => setForm({ ...form, kb_access })}
            knowledgeAppendEnabled={form.knowledge_append}
            disabled={isDisabled}
          />
        </section>

        {/* Rate limit */}
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_section_rate_limit()}
          </h2>
          <div className="space-y-1.5">
            <Label htmlFor="rate-limit">
              {m.admin_integrations_field_rate_limit()}
            </Label>
            <div className="flex items-center gap-2">
              <Input
                id="rate-limit"
                type="number"
                min={10}
                max={600}
                value={form.rate_limit_rpm}
                onChange={(e) =>
                  setForm({ ...form, rate_limit_rpm: Number(e.target.value) })
                }
                disabled={isDisabled}
                className="max-w-[8rem]"
              />
              <span className="text-sm text-[var(--color-muted-foreground)]">
                {m.admin_integrations_rate_limit_unit()}
              </span>
            </div>
          </div>
        </section>

        {updateMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">
            {updateMutation.error instanceof Error
              ? updateMutation.error.message
              : m.admin_integrations_error_generic()}
          </p>
        )}

        {!isRevoked && (
          <div className="pt-2">
            <Button
              type="submit"
              disabled={updateMutation.isPending || !form.name.trim()}
            >
              {updateMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {m.admin_integrations_save()}
            </Button>
          </div>
        )}
      </form>

      {/* View logs link */}
      <div className="mt-8 pt-6 border-t border-[var(--color-border)]">
        <a
          href={grafanaUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-sm text-[var(--color-accent)] hover:opacity-70 transition-opacity"
        >
          {m.admin_integrations_view_logs()}
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      </div>

      {/* Revoke section */}
      {!isRevoked && (
        <div className="mt-6 pt-6 border-t border-[var(--color-border)]">
          <h2 className="text-sm font-medium text-[var(--color-destructive)] mb-2">
            {m.admin_integrations_revoke_section_title()}
          </h2>
          <p className="text-sm text-[var(--color-muted-foreground)] mb-4">
            {m.admin_integrations_revoke_section_description()}
          </p>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={() => setShowRevokeDialog(true)}
          >
            {m.admin_integrations_revoke_button()}
          </Button>
        </div>
      )}

      <RevokeConfirmDialog
        open={showRevokeDialog}
        isPending={revokeMutation.isPending}
        onConfirm={handleRevoke}
        onCancel={() => setShowRevokeDialog(false)}
      />
    </div>
  )
}
