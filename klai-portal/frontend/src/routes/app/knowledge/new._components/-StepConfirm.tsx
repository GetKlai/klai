import { Brain, Globe, Lock, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import * as m from '@/paraglide/messages'
import type { WizardData, WizardErrorKey } from '../new._types'

export function StepConfirm({
  data,
  isPending,
  errorKey,
  canCreateKB,
  onSubmit,
  onEditSlug,
}: {
  data: WizardData
  isPending: boolean
  errorKey: WizardErrorKey
  canCreateKB: boolean
  onSubmit: () => void
  onEditSlug: () => void
}) {
  const visibilityLabel =
    data.visibilityMode === 'public'
      ? m.knowledge_sharing_visibility_public()
      : data.visibilityMode === 'org'
        ? m.knowledge_sharing_visibility_org()
        : m.knowledge_sharing_visibility_restricted()

  const VisibilityIcon =
    data.visibilityMode === 'public' ? Globe : data.visibilityMode === 'org' ? Users : Lock

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm font-medium text-gray-900">
        {m.knowledge_wizard_confirm_title()}
      </p>

      <Card>
        <CardContent className="pt-4">
          <div className="space-y-3 text-sm">
            <div className="flex items-start gap-2">
              <Brain className="h-4 w-4 mt-0.5 text-gray-400" />
              <div>
                <p className="font-medium text-gray-900">{data.name}</p>
                <p className="text-xs text-gray-400">{data.slug}</p>
              </div>
            </div>

            {data.description && (
              <p className="text-gray-400 italic">
                &ldquo;{data.description}&rdquo;
              </p>
            )}

            {data.ownerType === 'org' && (
              <div className="flex items-center gap-2">
                <VisibilityIcon className="h-4 w-4 text-gray-400" />
                <span className="text-gray-900">{visibilityLabel}</span>
              </div>
            )}

            {data.ownerType === 'org' && data.visibilityMode !== 'restricted' && (
              <p className="text-gray-400">
                {m.knowledge_sharing_summary_org_default({
                  role: data.allowContribute ? 'contributor' : 'viewer',
                })}
              </p>
            )}

            {data.ownerType === 'user' && (
              <p className="text-gray-400">
                {m.knowledge_wizard_personal_only()}
              </p>
            )}

            {data.ownerType === 'org' &&
              (data.initialGroups.length > 0 || data.initialUsers.length > 0) && (
                <div className="border-t border-gray-200 pt-2">
                  {data.visibilityMode === 'restricted' ? (
                    <p className="text-xs font-medium text-gray-400 mb-1">
                      {m.knowledge_sharing_summary_only_shared()}
                    </p>
                  ) : (
                    <p className="text-xs font-medium text-gray-400 mb-1">
                      {m.knowledge_wizard_extra_permissions_title()}:
                    </p>
                  )}
                  {data.initialGroups.map((g) => (
                    <p key={g.id} className="text-xs text-gray-400 pl-3">
                      &bull; {g.name} ({g.role})
                    </p>
                  ))}
                  {data.initialUsers.map((u) => (
                    <p key={u.id} className="text-xs text-gray-400 pl-3">
                      &bull; {u.name || u.email} ({u.role})
                    </p>
                  ))}
                </div>
              )}

            <p className="text-gray-400">
              {m.knowledge_sharing_summary_docs_auto()}
            </p>
          </div>
        </CardContent>
      </Card>

      {errorKey === 'conflict' && (
        <div className="flex items-center gap-2 text-sm">
          <p className="text-[var(--color-destructive)]">
            {m.knowledge_new_slug_conflict()}
          </p>
          <Button type="button" variant="link" size="sm" onClick={onEditSlug} className="px-0">
            {m.knowledge_wizard_edit_slug()}
          </Button>
        </div>
      )}
      {errorKey === 'generic' && (
        <p className="text-sm text-[var(--color-destructive)]">{m.knowledge_new_error()}</p>
      )}

      {data.ownerType === 'user' && !canCreateKB && (
        <p className="text-sm text-gray-400 opacity-70">
          {m.kb_limit_tooltip_kb_count()}
        </p>
      )}

      <div className="flex justify-end pt-2">
        <Button onClick={onSubmit} disabled={isPending || (data.ownerType === 'user' && !canCreateKB)}>
          {m.knowledge_wizard_create_button()}
        </Button>
      </div>
    </div>
  )
}
