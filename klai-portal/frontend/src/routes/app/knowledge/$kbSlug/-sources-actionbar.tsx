/**
 * Top-of-tab action bar for the Sources list.
 *
 * Three slots, right-aligned:
 *   1. "Open in editor" - only when docs are enabled AND there are pages.
 *   2. "Synchroniseer alles" - only when there is at least one connector source.
 *   3. "Bron toevoegen" - always.
 *
 * Left of the slots: subtle count ("N bronnen" / "N bron").
 */
import { Link } from '@tanstack/react-router'
import { Loader2, NotebookPen, Plus, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/ui/page-header'
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
  const sourceCount =
    sources.length === 1
      ? m.kb_count_bron_singular()
      : m.kb_count_bronnen({ count: String(sources.length) })

  return (
    <PageHeader
      title={m.kb_tab_sources()}
      description={sourceCount}
      actions={
        <div className="flex items-center gap-2">
          {showEditorLink && (
            <Button asChild variant="ghost" size="sm">
              <Link to="/app/docs/$kbSlug" params={{ kbSlug }}>
                <NotebookPen className="h-4 w-4" />
                {m.kb_sources_action_open_editor()}
              </Link>
            </Button>
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
              {m.kb_sources_action_sync_all()}
            </Button>
          )}
          <Button asChild variant="default" size="sm">
            <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }}>
              <Plus className="h-4 w-4" />
              {m.kb_sources_action_add()}
            </Link>
          </Button>
        </div>
      }
    />
  )
}
