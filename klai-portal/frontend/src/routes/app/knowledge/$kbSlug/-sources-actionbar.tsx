/**
 * Top-of-tab action bar for the Sources list.
 *
 * Three slots, right-aligned:
 *   1. "Open in editor" — only when docs are enabled AND there are pages.
 *   2. "Synchroniseer alles" — only when there is at least one connector source.
 *   3. "Bron toevoegen" — always.
 *
 * Left of the slots: subtle count ("N bronnen" / "N bron").
 */
import { Link } from '@tanstack/react-router'
import { Loader2, Pencil, Plus, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { useSyncAllConnectors } from './-sources-hooks'
import type { Source } from './-sources-types'

interface SourcesActionBarProps {
  kbSlug: string
  sources: Source[]
  connectorSources: Source[]
  showEditorLink: boolean
}

export function SourcesActionBar({
  kbSlug,
  sources,
  connectorSources,
  showEditorLink,
}: SourcesActionBarProps) {
  const syncAll = useSyncAllConnectors(kbSlug, connectorSources)
  return (
    <div className="flex items-center justify-between gap-4 mb-4">
      <p className="text-sm text-gray-400">
        {sources.length === 1
          ? m.kb_count_bron_singular()
          : m.kb_count_bronnen({ count: String(sources.length) })}
      </p>
      <div className="flex items-center gap-2">
        {showEditorLink && (
          <Link to="/app/docs/$kbSlug" params={{ kbSlug }}>
            <Button variant="ghost" size="sm">
              <Pencil className="h-4 w-4" />
              Open in editor
            </Button>
          </Link>
        )}
        {connectorSources.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => syncAll.mutate()}
            disabled={syncAll.isPending}
          >
            {syncAll.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4" />
            )}
            Synchroniseer alles
          </Button>
        )}
        <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }}>
          <Button variant="default" size="sm">
            <Plus className="h-4 w-4" />
            Bron toevoegen
          </Button>
        </Link>
      </div>
    </div>
  )
}
