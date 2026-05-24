import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { buildWidgetEmbedSnippet } from '@/features/widgets/embed/snippet'
import * as m from '@/paraglide/messages'

interface EmbedSnippetProps {
  widgetId: string
  title?: string
  welcomeMessage?: string
}

export function EmbedSnippet({ widgetId, title, welcomeMessage }: EmbedSnippetProps) {
  const [copied, setCopied] = useState(false)

  const snippet = buildWidgetEmbedSnippet(widgetId, title, welcomeMessage)

  function handleCopy() {
    void navigator.clipboard.writeText(snippet).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-900">
          {m.admin_widgets_widget_embed_title()}
        </span>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={handleCopy}
          className="h-7 text-xs gap-1.5"
        >
          {copied ? (
            <>
              <Check className="h-3.5 w-3.5 text-[var(--color-success)]" />
              {m.admin_widgets_widget_embed_copied()}
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              {m.admin_widgets_widget_embed_copy()}
            </>
          )}
        </Button>
      </div>
      <pre className="rounded-md border border-gray-200 bg-[var(--color-muted)] px-4 py-3 text-xs font-mono text-gray-900 overflow-x-auto whitespace-pre">
        {snippet}
      </pre>
    </div>
  )
}
