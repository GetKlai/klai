import { useState } from 'react'
import { Copy, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'

interface EmbedSnippetProps {
  widgetId: string
  title?: string
  welcomeMessage?: string
}

function buildSnippet(widgetId: string, title?: string, welcomeMessage?: string): string {
  const attrs: string[] = [`  src="https://my.getklai.com/widget/klai-chat.js"`]
  attrs.push(`  data-widget-id="${widgetId}"`)
  if (title) attrs.push(`  data-title="${title}"`)
  if (welcomeMessage) attrs.push(`  data-welcome="${welcomeMessage}"`)
  return `<script\n${attrs.join('\n')}\n></script>`
}

export function EmbedSnippet({ widgetId, title, welcomeMessage }: EmbedSnippetProps) {
  const [copied, setCopied] = useState(false)

  const snippet = buildSnippet(widgetId, title, welcomeMessage)

  function handleCopy() {
    void navigator.clipboard.writeText(snippet).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-[var(--color-foreground)]">
          {m.admin_integrations_widget_embed_title()}
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
              {m.admin_integrations_widget_embed_copied()}
            </>
          ) : (
            <>
              <Copy className="h-3.5 w-3.5" />
              {m.admin_integrations_widget_embed_copy()}
            </>
          )}
        </Button>
      </div>
      <pre className="rounded-md border border-[var(--color-border)] bg-[var(--color-muted)] px-4 py-3 text-xs font-mono text-[var(--color-foreground)] overflow-x-auto whitespace-pre">
        {snippet}
      </pre>
    </div>
  )
}
