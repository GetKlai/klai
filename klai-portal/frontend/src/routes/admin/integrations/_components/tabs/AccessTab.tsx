import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { AccessLevel, IntegrationDetailResponse } from '../../-types'
import { useUpdateIntegration } from '../../-hooks'
import { KbAccessEditor } from '../KbAccessEditor'

interface AccessTabProps {
  integration: IntegrationDetailResponse
}

interface AccessState {
  chat: boolean
  feedback: boolean
  knowledge_append: boolean
  kb_access: { kb_id: number; access_level: AccessLevel }[]
}

function mapFromIntegration(integration: IntegrationDetailResponse): AccessState {
  return {
    chat: integration.permissions.chat,
    feedback: integration.permissions.feedback,
    knowledge_append: integration.permissions.knowledge_append,
    kb_access: integration.kb_access.map((ka) => ({
      kb_id: ka.kb_id,
      access_level: ka.access_level,
    })),
  }
}

export function AccessTab({ integration }: AccessTabProps) {
  const updateMutation = useUpdateIntegration(String(integration.id))
  const isWidget = integration.integration_type === 'widget'
  const isDisabled = integration.active === false || updateMutation.isPending

  const [state, setState] = useState<AccessState>(() =>
    mapFromIntegration(integration),
  )

  useEffect(() => {
    setState(mapFromIntegration(integration))
  }, [integration])

  function handleKnowledgeAppendChange(checked: boolean) {
    setState((prev) => ({
      ...prev,
      knowledge_append: checked,
      kb_access: checked
        ? prev.kb_access
        : prev.kb_access.map((row) =>
            row.access_level === 'read_write'
              ? { ...row, access_level: 'read' as AccessLevel }
              : row,
          ),
    }))
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const payload = isWidget
      ? { kb_access: state.kb_access }
      : {
          permissions: {
            chat: state.chat,
            feedback: state.feedback,
            knowledge_append: state.knowledge_append,
          },
          kb_access: state.kb_access,
        }
    updateMutation.mutate(payload, {
      onSuccess: () => {
        toast.success(m.admin_integrations_success_updated())
      },
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Permissions — API only */}
      {!isWidget && (
        <section className="space-y-4">
          <h2 className="text-sm font-medium text-[var(--color-foreground)]">
            {m.admin_integrations_section_permissions()}
          </h2>
          <div className="space-y-4">
            <label className="flex items-start gap-2 text-sm text-[var(--color-foreground)]">
              <input
                type="checkbox"
                checked={state.chat}
                onChange={(e) =>
                  setState((prev) => ({ ...prev, chat: e.target.checked }))
                }
                disabled={isDisabled}
                className="accent-[var(--color-accent)] mt-0.5"
              />
              <div>
                <span className="font-medium">{m.admin_integrations_perm_chat()}</span>
                <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                  {m.admin_integrations_perm_chat_description()}
                </p>
              </div>
            </label>
            <label className="flex items-start gap-2 text-sm text-[var(--color-foreground)]">
              <input
                type="checkbox"
                checked={state.feedback}
                onChange={(e) =>
                  setState((prev) => ({ ...prev, feedback: e.target.checked }))
                }
                disabled={isDisabled}
                className="accent-[var(--color-accent)] mt-0.5"
              />
              <div>
                <span className="font-medium">{m.admin_integrations_perm_feedback()}</span>
                <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                  {m.admin_integrations_perm_feedback_description()}
                </p>
              </div>
            </label>
            <label className="flex items-start gap-2 text-sm text-[var(--color-foreground)]">
              <input
                type="checkbox"
                checked={state.knowledge_append}
                onChange={(e) => handleKnowledgeAppendChange(e.target.checked)}
                disabled={isDisabled}
                className="accent-[var(--color-accent)] mt-0.5"
              />
              <div>
                <span className="font-medium">{m.admin_integrations_perm_knowledge_append()}</span>
                <p className="text-xs text-[var(--color-muted-foreground)] mt-0.5">
                  {m.admin_integrations_perm_knowledge_append_description()}
                </p>
              </div>
            </label>
          </div>
        </section>
      )}

      {/* KB Access */}
      <section className="space-y-4">
        <h2 className="text-sm font-medium text-[var(--color-foreground)]">
          {m.admin_integrations_section_kb_access()}
        </h2>
        <p className="text-xs text-[var(--color-muted-foreground)]">
          {isWidget
            ? m.admin_integrations_wizard_kb_access_intro_widget()
            : m.admin_integrations_kb_intro()}
        </p>
        <KbAccessEditor
          value={state.kb_access}
          onChange={(kb_access) => setState((prev) => ({ ...prev, kb_access }))}
          knowledgeAppendEnabled={state.knowledge_append}
          disabled={isDisabled}
          hideReadWrite={isWidget}
        />
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_integrations_error_generic()}
        </p>
      )}

      {integration.active && (
        <div className="pt-2">
          <Button type="submit" disabled={updateMutation.isPending}>
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
