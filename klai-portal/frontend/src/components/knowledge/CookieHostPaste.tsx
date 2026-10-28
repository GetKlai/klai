/**
 * CookieHostPaste - paste a Cookie-Editor JSON export for the site's host, and
 * list the pasted cookies by name and expiry (never by value).
 *
 * Every cookie keeps the domain it was exported with; the backend refuses one
 * whose domain does not cover the site's host.
 */
import { useState } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import type { CookieRow } from '@/routes/app/knowledge/$kbSlug/-kb-types'
import {
  CookieJsonError,
  cookieCoversHost,
  isManualRow,
  parseCookieEditorJson,
} from '@/routes/app/knowledge/-connector-shared/cookieHosts'

interface CookieHostPasteProps {
  host: string
  /** All cookie rows of the wizard. */
  rows: CookieRow[]
  onChange: (rows: CookieRow[]) => void
  idPrefix: string
}

function sameCookie(a: CookieRow, b: CookieRow): boolean {
  return a.name === b.name && (a.domain ?? '') === (b.domain ?? '') && (a.path ?? '/') === (b.path ?? '/')
}

export function CookieHostPaste({ host, rows, onChange, idPrefix }: CookieHostPasteProps) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const listed = rows.filter((row) => !isManualRow(row))

  function addCookies() {
    let parsed: CookieRow[]
    try {
      parsed = parseCookieEditorJson(text, host)
    } catch (e) {
      if (!(e instanceof CookieJsonError)) throw e
      setError(m.admin_connectors_webcrawler_cookies_json_invalid({ reason: e.message }))
      return
    }
    const foreign = parsed.filter((c) => !cookieCoversHost(c.domain ?? '', host))
    if (foreign.length > 0) {
      setError(
        m.admin_connectors_webcrawler_cookies_json_other_host({
          names: foreign.map((c) => `${c.name} (${c.domain})`).join(', '),
        }),
      )
      return
    }
    setError(null)
    setText('')
    onChange([...rows.filter((row) => !parsed.some((c) => sameCookie(c, row))), ...parsed])
  }

  return (
    <div className="space-y-2">
      {listed.length > 0 && (
        <ul className="space-y-1">
          {listed.map((row) => (
            <li key={`${row.name}|${row.domain}|${row.path}`} className="flex items-center gap-2 text-xs text-gray-900">
              <span className="font-mono">{row.name}</span>
              <span className="text-gray-600">{row.domain}</span>
              <span className="text-gray-600">
                {row.expires
                  ? m.admin_connectors_webcrawler_cookie_expires({
                      date: new Date(row.expires).toLocaleDateString(getLocale()),
                    })
                  : row.imported
                    ? m.admin_connectors_webcrawler_cookie_session()
                    : m.admin_connectors_webcrawler_cookie_saved()}
              </span>
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={m.admin_connectors_webcrawler_cookie_remove({ name: row.name })}
                onClick={() => onChange(rows.filter((r) => r !== row))}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Textarea
        id={`${idPrefix}-json`}
        rows={3}
        value={text}
        spellCheck={false}
        placeholder={m.admin_connectors_webcrawler_cookies_json_placeholder()}
        className="font-mono text-xs"
        aria-label={m.admin_connectors_webcrawler_cookies_json_label({ host })}
        onChange={(e) => setText(e.target.value)}
      />
      {error && <p className="text-xs text-[var(--color-destructive)]">{error}</p>}
      <Button type="button" variant="outline" size="sm" disabled={!text.trim()} onClick={addCookies}>
        {m.admin_connectors_webcrawler_cookies_json_add()}
      </Button>
    </div>
  )
}
