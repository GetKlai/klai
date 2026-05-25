/**
 * CookieRowsInput - structured cookie input for the connector wizard.
 *
 * Replaces the free-text "paste your Cookie header here" textarea that
 * forced the frontend to parse a string into the structured shape the
 * backend expects. The wizard now collects the same shape directly from
 * the operator: one row per cookie, with explicit name + value fields.
 *
 * No parser. No silent fallbacks. No name='session' guessing. Operators
 * type the cookie name they actually see in DevTools - exact match with
 * what the connector stores in DB and what the cron-sync passes through.
 *
 * Always renders at least one row so first-time use is obvious. Remove
 * button hidden when only one row is visible (you can't remove the last
 * row; you clear it instead).
 */
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Plus, X } from 'lucide-react'
import type { CookieRow } from '@/routes/app/knowledge/$kbSlug/-kb-types'

interface CookieRowsInputProps {
  /** Current cookie rows. Empty array → one empty row is rendered. */
  value: CookieRow[]
  /** Called with the full updated rows array on every change. */
  onChange: (rows: CookieRow[]) => void
  /** Optional id-prefix so the component can be used twice on the same page. */
  idPrefix?: string
}

export function CookieRowsInput({
  value,
  onChange,
  idPrefix = 'cookie-row',
}: CookieRowsInputProps) {
  // Always show at least one row - empty input is the first-time-use UX.
  const rows: CookieRow[] = value.length === 0 ? [{ name: '', value: '' }] : value
  const showRemove = rows.length > 1

  function updateRow(index: number, patch: Partial<CookieRow>) {
    const next = rows.map((row, i) => (i === index ? { ...row, ...patch } : row))
    onChange(next)
  }

  function addRow() {
    onChange([...rows, { name: '', value: '' }])
  }

  function removeRow(index: number) {
    const next = rows.filter((_, i) => i !== index)
    // Never push an empty array upstream - keep one row visible.
    onChange(next.length === 0 ? [{ name: '', value: '' }] : next)
  }

  return (
    <div className="space-y-3">
      {rows.map((row, index) => {
        const nameId = `${idPrefix}-name-${index}`
        const valueId = `${idPrefix}-value-${index}`
        return (
          <div key={index} className="flex items-end gap-2">
            <div className="flex-shrink-0 w-1/3 space-y-1">
              {index === 0 && (
                <Label htmlFor={nameId} className="text-xs">
                  Cookie name
                </Label>
              )}
              <Input
                id={nameId}
                type="text"
                placeholder="e.g. prod-knowledgebase-session"
                value={row.name}
                spellCheck={false}
                autoCorrect="off"
                autoCapitalize="off"
                className="font-mono text-xs"
                onChange={(e) => updateRow(index, { name: e.target.value })}
              />
            </div>
            <div className="flex-1 space-y-1">
              {index === 0 && (
                <Label htmlFor={valueId} className="text-xs">
                  Cookie value
                </Label>
              )}
              <Input
                id={valueId}
                type="text"
                placeholder="e.g. eyJhbGciOi..."
                value={row.value}
                spellCheck={false}
                autoCorrect="off"
                autoCapitalize="off"
                className="font-mono text-xs"
                onChange={(e) => updateRow(index, { value: e.target.value })}
              />
            </div>
            {showRemove && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={`Remove cookie ${index + 1}`}
                onClick={() => removeRow(index)}
              >
                <X className="h-4 w-4" />
              </Button>
            )}
          </div>
        )
      })}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={addRow}
      >
        <Plus className="h-3.5 w-3.5 mr-1" />
        Add another cookie
      </Button>
      <p className="text-xs text-gray-400">
        Open the site in your browser, log in, then read the cookie name and
        value from DevTools &rarr; Application &rarr; Cookies. Type or paste
        each separately - no need to format anything.
      </p>
    </div>
  )
}
