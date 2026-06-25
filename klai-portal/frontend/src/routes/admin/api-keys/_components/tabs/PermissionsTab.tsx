import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { ApiKeyDetailResponse } from '../../-types'
import { useUpdateApiKey } from '../../-hooks'

interface Props {
  apiKey: ApiKeyDetailResponse
}

export function PermissionsTab({ apiKey }: Props) {
  const updateMutation = useUpdateApiKey(String(apiKey.id))
  const [chat, setChat] = useState(apiKey.permissions.chat)
  const [feedback, setFeedback] = useState(apiKey.permissions.feedback)
  const [knowledgeAppend, setKnowledgeAppend] = useState(apiKey.permissions.knowledge_append)
  const [generalChat, setGeneralChat] = useState(Boolean(apiKey.permissions.general_chat))

  useEffect(() => {
    setChat(apiKey.permissions.chat)
    setFeedback(apiKey.permissions.feedback)
    setKnowledgeAppend(apiKey.permissions.knowledge_append)
    setGeneralChat(Boolean(apiKey.permissions.general_chat))
  }, [apiKey.permissions])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    updateMutation.mutate(
      {
        permissions: {
          chat,
          feedback,
          knowledge_append: knowledgeAppend,
          general_chat: generalChat,
        },
      },
      {
        onSuccess: () => toast.success(m.admin_shared_success_updated()),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <div className="space-y-4">
          <label className="flex items-start gap-2 text-sm text-gray-900">
            <input
              type="checkbox"
              checked={chat}
              onChange={(e) => setChat(e.target.checked)}
              className="accent-[var(--color-accent)] mt-0.5"
            />
            <div>
              <span className="font-medium">{m.admin_api_keys_perm_chat()}</span>
              <p className="text-xs text-gray-400 mt-0.5">
                {m.admin_api_keys_perm_chat_description()}
              </p>
            </div>
          </label>
          <label className="flex items-start gap-2 text-sm text-gray-900">
            <input
              type="checkbox"
              checked={feedback}
              onChange={(e) => setFeedback(e.target.checked)}
              className="accent-[var(--color-accent)] mt-0.5"
            />
            <div>
              <span className="font-medium">{m.admin_api_keys_perm_feedback()}</span>
              <p className="text-xs text-gray-400 mt-0.5">
                {m.admin_api_keys_perm_feedback_description()}
              </p>
            </div>
          </label>
          <label className="flex items-start gap-2 text-sm text-gray-900">
            <input
              type="checkbox"
              checked={knowledgeAppend}
              onChange={(e) => setKnowledgeAppend(e.target.checked)}
              className="accent-[var(--color-accent)] mt-0.5"
            />
            <div>
              <span className="font-medium">{m.admin_api_keys_perm_knowledge_append()}</span>
              <p className="text-xs text-gray-400 mt-0.5">
                {m.admin_api_keys_perm_knowledge_append_description()}
              </p>
            </div>
          </label>
          <label className="flex items-start gap-2 text-sm text-gray-900">
            <input
              type="checkbox"
              checked={generalChat}
              onChange={(e) => setGeneralChat(e.target.checked)}
              className="accent-[var(--color-accent)] mt-0.5"
            />
            <div>
              <span className="font-medium">{m.admin_api_keys_perm_general_chat()}</span>
              <p className="text-xs text-gray-400 mt-0.5">
                {m.admin_api_keys_perm_general_chat_description()}
              </p>
            </div>
          </label>
        </div>
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_shared_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button type="submit" disabled={updateMutation.isPending}>
          {updateMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          {m.admin_shared_save()}
        </Button>
      </div>
    </form>
  )
}
