/**
 * Top-of-tab action bar for the Sources list.
 *
 * Slots, right-aligned:
 *   1. "Support cases" - only for an organisation KB. A personal (user-owned)
 *      KB has no support inbox, and the target screen is org-only, so the link
 *      stays hidden while ownership is unknown or user-owned.
 *   2. "Open in editor" - only when docs are enabled AND there are pages.
 *   3. "Synchroniseer alles" - only when there is at least one connector source.
 *   4. "Bron toevoegen" - always.
 *
 * Left of the slots: subtle count ("N bronnen" / "N bron").
 */
import { Link } from '@tanstack/react-router'
import { FileText, Loader2, NotebookPen, Plus, RefreshCw } from 'lucide-react'
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
  showSupportCases: boolean
}

export function SourcesActionBar({
  kbSlug,
  sources,
  connectorSources,
  showEditorLink,
  showSupportCases,
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
          {showSupportCases && (
            <Button asChild variant="outline" size="sm">
              <Link to="/app/knowledge/gaps/support-cases" search={{ kbSlug }}>
                <FileText className="h-4 w-4" />
                {m.support_cases_action_open()}
              </Link>
            </Button>
          )}
          {showEditorLink && (
            <Button asChild variant="outline" size="sm">
              <Link to="/app/docs/$kbSlug" params={{ kbSlug }}>
                <NotebookPen className="h-4 w-4" />
                {m.kb_sources_action_open_editor()}
              </Link>
            </Button>
          )}
          {connectorSources.length > 0 && (
            <Button
              variant="outline"
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
