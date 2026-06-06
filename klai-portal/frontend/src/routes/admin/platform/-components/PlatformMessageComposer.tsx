import { useState } from 'react'
import { Loader2, MessageSquare, Send } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import * as m from '@/paraglide/messages'
import { usePlatformCreateMessageThread } from '../-hooks'
import type { PlatformUser } from '../-types'

export function PlatformMessageComposer({
  user,
  triggerClassName,
}: {
  user: PlatformUser
  triggerClassName?: string
}) {
  const createThread = usePlatformCreateMessageThread()
  const [open, setOpen] = useState(false)
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const recipient = user.display_name || user.email || user.zitadel_user_id
  const canSubmit = subject.trim().length > 0 && body.trim().length > 0

  function submit() {
    if (!canSubmit) return
    createThread.mutate(
      {
        org_id: user.org_id,
        user_ids: [user.zitadel_user_id],
        subject: subject.trim(),
        body: body.trim(),
      },
      {
        onSuccess: () => {
          toast.success(m.platform_messages_sent())
          setOpen(false)
          setSubject('')
          setBody('')
        },
        onError: (error) =>
          toast.error(error instanceof Error ? error.message : m.admin_shared_error_generic()),
      },
    )
  }

  return (
    <>
      <Button
        type="button"
        variant="link"
        onClick={(event) => {
          event.stopPropagation()
          setOpen(true)
        }}
        className={triggerClassName ?? 'h-auto p-0 text-xs font-medium no-underline hover:no-underline'}
      >
        <MessageSquare className="h-3.5 w-3.5" />
        {m.platform_messages_send_action()}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{m.platform_messages_compose_title()}</DialogTitle>
            <DialogDescription>
              {m.platform_messages_compose_description({ recipient })}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor={`platform-message-subject-${user.zitadel_user_id}`}>
                {m.platform_messages_subject()}
              </Label>
              <Input
                id={`platform-message-subject-${user.zitadel_user_id}`}
                value={subject}
                onChange={(event) => setSubject(event.target.value)}
                maxLength={256}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`platform-message-body-${user.zitadel_user_id}`}>
                {m.platform_messages_body()}
              </Label>
              <Textarea
                id={`platform-message-body-${user.zitadel_user_id}`}
                value={body}
                onChange={(event) => setBody(event.target.value)}
                rows={5}
                maxLength={4000}
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
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
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
