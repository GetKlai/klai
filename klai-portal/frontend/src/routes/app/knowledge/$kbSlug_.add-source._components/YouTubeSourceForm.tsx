import { useState, type FormEvent } from 'react'
import { CheckCircle2 } from 'lucide-react'
import { SiYoutube } from '@icons-pack/react-simple-icons'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import { useSourceSubmit } from './useSourceSubmit'

interface YouTubeSourceFormProps {
  kbSlug: string
  onBack: () => void
}

interface YouTubeBody {
  url: string
}

export function YouTubeSourceForm({ kbSlug, onBack }: YouTubeSourceFormProps) {
  const [url, setUrl] = useState('')
  const { mutation, errorMessage, successful } = useSourceSubmit<YouTubeBody>({
    kbSlug,
    kind: 'youtube',
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    mutation.mutate({ url: trimmed })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="flex items-center gap-2 text-gray-400">
        <SiYoutube className="h-5 w-5" />
        <p className="text-sm">{m.knowledge_add_source_youtube_hint()}</p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="source-youtube">
          {m.knowledge_add_source_youtube_label()}
        </Label>
        <Input
          id="source-youtube"
          type="url"
          autoFocus
          required
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={m.knowledge_add_source_youtube_placeholder()}
          disabled={mutation.isPending || successful}
        />
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
        <Button
          type="submit"
          disabled={!url.trim() || mutation.isPending || successful}
        >
          {mutation.isPending
            ? m.knowledge_add_source_submitting()
            : m.knowledge_add_source_submit_youtube()}
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
