import { Loader2 } from 'lucide-react'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'

interface RevokedProps {
  status: 'revoked'
  onReconnect: () => void
  isReconnecting: boolean
}

export function SlackStatusAlert(props: { status: 'awaiting_link' } | RevokedProps) {
  if (props.status === 'awaiting_link') {
    return (
      <Alert variant="warning">
        <p>{m.admin_slack_legacy_body()}</p>
      </Alert>
    )
  }

  return (
    <Alert variant="warning">
      <p>{m.admin_slack_revoked_body()}</p>
      <Button
        type="button"
        size="sm"
        className="mt-3"
        onClick={props.onReconnect}
        disabled={props.isReconnecting}
      >
        {props.isReconnecting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
        {m.admin_slack_reconnect()}
      </Button>
    </Alert>
  )
}
