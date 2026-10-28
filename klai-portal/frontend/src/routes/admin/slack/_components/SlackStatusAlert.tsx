import { Loader2 } from 'lucide-react'
import { Alert } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'

interface Props {
  onReconnect: () => void
  isReconnecting: boolean
}

export function SlackStatusAlert(props: Props) {
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
