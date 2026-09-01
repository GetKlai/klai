import { useState, type FormEvent } from 'react'
import { CheckCircle2, Link2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { useSourceSubmit } from './useSourceSubmit'

interface UrlSourceFormProps {
  kbSlug: string
  onBack: () => void
}

interface UrlBody {
  url: string
}

export function UrlSourceForm({ kbSlug, onBack }: UrlSourceFormProps) {
  const [url, setUrl] = useState('')
  const { mutation, errorMessage, successful } = useSourceSubmit<UrlBody>({
    kbSlug,
    kind: 'url',
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    mutation.mutate({ url: trimmed })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="flex items-center gap-2 text-gray-600">
        <Link2 className="h-5 w-5" />
        <p className="text-sm">{m.knowledge_add_source_url_hint()}</p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="source-url">{m.knowledge_add_source_url_label()}</Label>
        <Input
          id="source-url"
          type="url"
          autoFocus
          required
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={m.knowledge_add_source_url_placeholder()}
          disabled={mutation.isPending || successful}
        />
      </div>

      {successful && (
        <div className="flex items-center gap-2 rounded-lg border border-[var(--color-success)] bg-[var(--color-success-bg)] px-4 py-3">
          <CheckCircle2 className="h-4 w-4 text-[var(--color-success-text)] shrink-0" />
          <p className="text-sm text-[var(--color-success-text)]">
            {m.knowledge_add_source_success()}
          </p>
        </div>
      )}

      {errorMessage && !successful && (
        <p className="text-sm text-[var(--color-destructive)]">{errorMessage}</p>
      )}

      <div className="flex items-center gap-3 pt-2">
        <Button
          type="submit"
          disabled={!url.trim() || mutation.isPending || successful}
        >
          {mutation.isPending
            ? m.knowledge_add_source_submitting()
            : m.knowledge_add_source_submit_url()}
        </Button>
        <button
          type="button"
          onClick={onBack}
          className="text-sm text-gray-600 hover:text-gray-900 transition-colors"
        >
          {m.knowledge_add_source_back()}
        </button>
      </div>
    </form>
  )
}
