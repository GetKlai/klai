import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { AccessLevel, ApiKeyDetailResponse } from '../../-types'
import { useUpdateApiKey } from '../../-hooks'
import { KbAccessEditor } from '../KbAccessEditor'

interface Props {
  apiKey: ApiKeyDetailResponse
}

export function KnowledgeBasesTab({ apiKey }: Props) {
  const updateMutation = useUpdateApiKey(String(apiKey.id))
  const [kbAccess, setKbAccess] = useState(
    apiKey.kb_access.map((ka) => ({
      kb_id: ka.kb_id,
      access_level: ka.access_level,
    })),
  )

  useEffect(() => {
    setKbAccess(
      apiKey.kb_access.map((ka) => ({
        kb_id: ka.kb_id,
        access_level: ka.access_level,
      })),
    )
  }, [apiKey.kb_access])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    updateMutation.mutate(
      { kb_access: kbAccess },
      {
        onSuccess: () => toast.success(m.admin_integrations_success_updated()),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.admin_integrations_wizard_kb_access_intro_api()}
        </p>
        <KbAccessEditor
          value={kbAccess}
          onChange={(v) => setKbAccess(v as { kb_id: number; access_level: AccessLevel }[])}
          knowledgeAppendEnabled={apiKey.permissions.knowledge_append}
        />
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_integrations_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button type="submit" disabled={updateMutation.isPending}>
          {updateMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          {m.admin_integrations_save()}
        </Button>
      </div>
    </form>
  )
}
