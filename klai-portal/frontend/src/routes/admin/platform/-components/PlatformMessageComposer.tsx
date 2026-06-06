import { useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { Loader2, Send } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { BorderedRowActionIconButton } from '@/components/ui/row-action'
import { Textarea } from '@/components/ui/textarea'
import * as m from '@/paraglide/messages'
import { usePlatformCreateMessageThread } from '../-hooks'
import type { PlatformUser } from '../-types'

export interface PlatformMessageComposeTarget {
  orgId: number
  userId: string
  recipient: string
}

export function PlatformMessageComposer({ user }: { user: PlatformUser }) {
  const navigate = useNavigate()
  const recipient = user.display_name || user.email || user.zitadel_user_id

  return (
    <BorderedRowActionIconButton
      label={m.platform_messages_send_action()}
      action="send"
      onClick={(event) => {
        event.stopPropagation()
        void navigate({
          to: '/admin/platform',
          search: {
            tab: 'messages',
            messageUserId: user.zitadel_user_id,
            messageOrgId: String(user.org_id),
            messageRecipient: recipient,
          },
        })
      }}
    />
  )
}

export function PlatformMessageComposerPanel({
  target,
  onCancel,
}: {
  target: PlatformMessageComposeTarget
  onCancel: () => void
}) {
  const createThread = usePlatformCreateMessageThread()
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const canSubmit = subject.trim().length > 0 && body.trim().length > 0

  function submit() {
    if (!canSubmit) return
    createThread.mutate(
      {
        org_id: target.orgId,
        user_ids: [target.userId],
        subject: subject.trim(),
        body: body.trim(),
      },
      {
        onSuccess: () => {
          toast.success(m.platform_messages_sent())
          setSubject('')
          setBody('')
          onCancel()
        },
        onError: (error) =>
          toast.error(error instanceof Error ? error.message : m.admin_shared_error_generic()),
      },
    )
  }

  return (
    <section className="space-y-4 border-b border-gray-200 pb-6">
      <div>
        <h2 className="text-base font-display-bold text-gray-900">
          {m.platform_messages_compose_title()}
        </h2>
        <p className="mt-1 text-sm text-gray-400">
          {m.platform_messages_compose_description({ recipient: target.recipient })}
        </p>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)_auto] lg:items-end">
        <div className="space-y-1.5">
          <Label htmlFor={`platform-message-subject-${target.userId}`}>
            {m.platform_messages_subject()}
          </Label>
          <Input
            id={`platform-message-subject-${target.userId}`}
            value={subject}
            onChange={(event) => setSubject(event.target.value)}
            maxLength={256}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`platform-message-body-${target.userId}`}>
            {m.platform_messages_body()}
          </Label>
          <Textarea
            id={`platform-message-body-${target.userId}`}
            value={body}
            onChange={(event) => setBody(event.target.value)}
            rows={3}
            maxLength={4000}
          />
        </div>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>
            {m.admin_users_cancel()}
          </Button>
          <Button type="button" disabled={!canSubmit || createThread.isPending} onClick={submit}>
            {createThread.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
            {m.platform_messages_send()}
          </Button>
        </div>
      </div>
    </section>
  )
}
