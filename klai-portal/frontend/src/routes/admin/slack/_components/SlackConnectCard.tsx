import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PageIntro } from '@/components/ui/page-header'
import { OrgKbChecklist } from '@/components/admin/OrgKbChecklist'
import * as m from '@/paraglide/messages'
import { useCreateSlackKey } from '../-hooks'

interface Props {
  /** Receives the plaintext key, which the API returns exactly once. */
  onCreated: (apiKey: string) => void
}

export function SlackConnectCard({ onCreated }: Props) {
  const createMutation = useCreateSlackKey()
  const [kbIds, setKbIds] = useState<number[]>([])

  function handleCreate() {
    createMutation.mutate(
      { kb_ids: kbIds },
      { onSuccess: (res) => onCreated(res.api_key) },
    )
  }

  return (
    <div className="space-y-6">
      <PageIntro>
        <p>{m.admin_slack_intro()}</p>
        <p>{m.admin_slack_kbs_intro()}</p>
      </PageIntro>

      <OrgKbChecklist value={kbIds} onChange={setKbIds} disabled={createMutation.isPending} />

      {createMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">{createMutation.error.message}</p>
      )}

      <div className="flex items-center gap-3">
        <Button
          type="button"
          onClick={handleCreate}
          disabled={kbIds.length === 0 || createMutation.isPending}
        >
          {createMutation.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
          {m.admin_slack_create_key()}
        </Button>
        {kbIds.length === 0 && (
          <span className="text-xs text-gray-600">{m.admin_slack_kb_required()}</span>
        )}
      </div>
    </div>
  )
}
