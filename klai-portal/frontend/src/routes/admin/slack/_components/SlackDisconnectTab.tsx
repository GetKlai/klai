import { useState } from 'react'
import { Loader2, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import * as m from '@/paraglide/messages'
import { useDisconnectSlack } from '../-hooks'

interface Props {
  /** True while only a key exists (no workspace linked yet): the action removes the key. */
  awaitingLink: boolean
}

export function SlackDisconnectTab({ awaitingLink }: Props) {
  const disconnectMutation = useDisconnectSlack()
  const [confirming, setConfirming] = useState(false)

  function handleDisconnect() {
    disconnectMutation.mutate(undefined, {
      onSuccess: (res) => {
        if (res?.slack_revoked === false) {
          toast.warning(m.admin_slack_disconnect_partial())
        } else {
          toast.success(
            awaitingLink
              ? m.admin_slack_remove_key_success()
              : m.admin_slack_disconnect_success(),
          )
        }
      },
    })
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="mb-2 text-sm font-medium text-[var(--color-destructive)]">
          {awaitingLink ? m.admin_slack_tab_remove_key() : m.admin_slack_disconnect_title()}
        </h2>
        <p className="mb-4 text-sm text-gray-600">
          {awaitingLink
            ? m.admin_slack_remove_key_description()
            : m.admin_slack_disconnect_description()}
        </p>
        <InlineDeleteConfirm
          isConfirming={confirming}
          isPending={disconnectMutation.isPending}
          label={
            awaitingLink
              ? m.admin_slack_remove_key_confirm()
              : m.admin_slack_disconnect_confirm()
          }
          cancelLabel={m.admin_users_cancel()}
          onConfirm={handleDisconnect}
          onCancel={() => setConfirming(false)}
        >
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={() => setConfirming(true)}
            disabled={disconnectMutation.isPending}
          >
            {disconnectMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Trash2 className="mr-2 h-4 w-4" />
            )}
            {awaitingLink
              ? m.admin_slack_remove_key_button()
              : m.admin_slack_disconnect_button()}
          </Button>
        </InlineDeleteConfirm>
      </div>
    </div>
  )
}
