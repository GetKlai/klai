import { useState, type FormEvent } from 'react'
import { CheckCircle2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { useSourceSubmit } from './useSourceSubmit'

interface TextSourceFormProps {
  kbSlug: string
  onBack: () => void
}

interface TextBody {
  title: string | null
  content: string
}

// IngestRequest.content has max_length=500_000 — enforce client-side so the
// user learns before a 400 from the backend. Same constant lives in
// app/services/source_extractors/text.py._MAX_TEXT_LEN.
const MAX_CONTENT_CHARS = 500_000

export function TextSourceForm({ kbSlug, onBack }: TextSourceFormProps) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const { mutation, errorMessage, successful } = useSourceSubmit<TextBody>({
    kbSlug,
    kind: 'text',
  })

  const trimmedContent = content.trim()
  const overLimit = content.length > MAX_CONTENT_CHARS
  const canSubmit =
    trimmedContent.length > 0 &&
    !overLimit &&
    !mutation.isPending &&
    !successful

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canSubmit) return
    mutation.mutate({
      title: title.trim() || null,
      content,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="space-y-1.5">
        <Label htmlFor="source-title">
          {m.knowledge_add_source_text_title_label()}
        </Label>
        <Input
          id="source-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={m.knowledge_add_source_text_title_placeholder()}
          disabled={mutation.isPending || successful}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="source-content">
          {m.knowledge_add_source_text_content_label()}
        </Label>
        <textarea
          id="source-content"
          required
          rows={12}
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder={m.knowledge_add_source_text_content_placeholder()}
          disabled={mutation.isPending || successful}
          className="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm text-gray-900 outline-none transition-colors placeholder:text-gray-400 focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50"
        />
        <p className="text-xs text-gray-400 tabular-nums">
          {content.length.toLocaleString()} / {MAX_CONTENT_CHARS.toLocaleString()}
        </p>
      </div>

      {successful && (
        <div className="flex items-center gap-2 rounded-lg border border-[var(--color-success)] bg-[var(--color-success-bg)] px-4 py-3">
          <CheckCircle2 className="h-4 w-4 text-[var(--color-success)] shrink-0" />
          <p className="text-sm text-[var(--color-success-text)]">
            {m.knowledge_add_source_success()}
          </p>
        </div>
      )}

      {errorMessage && !successful && (
        <p className="text-sm text-[var(--color-destructive)]">{errorMessage}</p>
      )}

      <div className="flex items-center gap-3 pt-2">
        <Button type="submit" disabled={!canSubmit}>
          {mutation.isPending
            ? m.knowledge_add_source_submitting()
            : m.knowledge_add_source_submit_text()}
        </Button>
        <button
          type="button"
          onClick={onBack}
          className="text-sm text-gray-400 hover:text-gray-900 transition-colors"
        >
          {m.knowledge_add_source_back()}
        </button>
      </div>
    </form>
  )
}
