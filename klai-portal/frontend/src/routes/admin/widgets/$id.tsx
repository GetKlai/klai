import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ArrowLeft, Info, Shield, Palette, Code2, AlertTriangle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { useWidget } from './-hooks'
import { DetailsTab } from './_components/tabs/DetailsTab'
import { KnowledgeBasesTab } from './_components/tabs/KnowledgeBasesTab'
import { AppearanceTab } from './_components/tabs/AppearanceTab'
import { EmbedTab } from './_components/tabs/EmbedTab'
import { DangerTab } from './_components/tabs/DangerTab'

type TabId = 'details' | 'kbs' | 'appearance' | 'embed' | 'danger'

const VALID_TABS = new Set<TabId>(['details', 'kbs', 'appearance', 'embed', 'danger'])

type DetailSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/admin/widgets/$id')({
  validateSearch: (search: Record<string, unknown>): DetailSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: WidgetDetailPage,
})

function WidgetDetailPage() {
  const { id } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate()

  const { data: widget, isLoading, error, refetch } = useWidget(id)

  const activeTab: TabId = search.tab ?? 'details'

  if (isLoading) {
    return (
      <div className="p-6">
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_widgets_loading()}
        </p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 max-w-lg">
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }

  if (!widget) return null

  const tabs: { id: TabId; label: string; icon: React.ElementType }[] = [
    { id: 'details', label: m.admin_integrations_tab_general(), icon: Info },
    { id: 'kbs', label: m.admin_integrations_wizard_step_kb_access(), icon: Shield },
    { id: 'appearance', label: m.admin_integrations_wizard_step_appearance(), icon: Palette },
    { id: 'embed', label: m.admin_integrations_wizard_step_embed(), icon: Code2 },
    { id: 'danger', label: m.admin_integrations_tab_danger(), icon: AlertTriangle },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/admin/widgets/$id',
      params: { id },
      search: { tab },
    })
  }

  return (
    <div className="p-6 max-w-4xl space-y-8">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
            {widget.name}
          </h1>
          {widget.description && (
            <p className="text-sm text-[var(--color-muted-foreground)] mt-1">
              {widget.description}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/widgets' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_widgets_back_to_list()}
        </Button>
      </div>

      <div className="border-b border-[var(--color-border)]">
        <nav className="-mb-px flex gap-6">
          {tabs.map(({ id: tabId, label, icon: TabIcon }) => {
            const isActive = tabId === activeTab
            return (
              <button
                key={tabId}
                type="button"
                onClick={() => setTab(tabId)}
                className={[
                  'flex items-center gap-1.5 pb-3 text-sm font-medium border-b-2 transition-colors',
                  isActive
                    ? 'border-[var(--color-accent)] text-[var(--color-foreground)]'
                    : 'border-transparent text-[var(--color-muted-foreground)] hover:text-[var(--color-foreground)]',
                ].join(' ')}
              >
                <TabIcon className="h-4 w-4" />
                {label}
              </button>
            )
          })}
        </nav>
      </div>

      {activeTab === 'details' && <DetailsTab widget={widget} />}
      {activeTab === 'kbs' && <KnowledgeBasesTab widget={widget} />}
      {activeTab === 'appearance' && <AppearanceTab widget={widget} />}
      {activeTab === 'embed' && <EmbedTab widget={widget} />}
      {activeTab === 'danger' && <DangerTab widget={widget} />}
    </div>
  )
}
