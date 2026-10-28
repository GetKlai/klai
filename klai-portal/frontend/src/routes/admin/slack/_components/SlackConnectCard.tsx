import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { OrgKbChecklist } from '@/components/admin/OrgKbChecklist'
import * as m from '@/paraglide/messages'
import { useConnectSlack } from '../-hooks'

export function SlackConnectCard() {
  const connectMutation = useConnectSlack()
  const [kbIds, setKbIds] = useState<number[]>([])
  // The page stays on screen until Slack takes over, so the button keeps
  // spinning after the request itself has succeeded.
  const busy = connectMutation.isPending || connectMutation.isSuccess

  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.admin_slack_connect_title()}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <OrgKbChecklist value={kbIds} onChange={setKbIds} disabled={busy} />
        <p className="text-sm text-gray-600">{m.admin_slack_connect_hint()}</p>

        {connectMutation.error && (
          <p className="text-sm text-[var(--color-destructive)]">{connectMutation.error.message}</p>
        )}

        <div className="flex flex-col items-start gap-2">
          <Button
            type="button"
            onClick={() => connectMutation.mutate({ kb_ids: kbIds })}
            disabled={kbIds.length === 0 || busy}
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" />}
            {m.admin_slack_connect_button()}
          </Button>
          <p className="text-xs text-gray-600">{m.admin_slack_connect_redirect_note()}</p>
        </div>
      </CardContent>
    </Card>
  )
}
