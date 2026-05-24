import { createFileRoute, useNavigate } from '@tanstack/react-router'
import {
  ArrowLeft,
  Info,
  Shield,
  Palette,
  Code2,
  Activity,
  AlertTriangle,
  Loader2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { useWidget } from './-hooks'
import { DetailsTab } from './_components/tabs/DetailsTab'
import { KnowledgeBasesTab } from './_components/tabs/KnowledgeBasesTab'
import { AppearanceTab } from './_components/tabs/AppearanceTab'
import { EmbedTab } from './_components/tabs/EmbedTab'
import { ActivityTab } from './_components/tabs/ActivityTab'
import { DangerTab } from './_components/tabs/DangerTab'

type TabId = 'details' | 'kbs' | 'appearance' | 'embed' | 'activity' | 'danger'

const VALID_TABS = new Set<TabId>([
  'details',
  'kbs',
  'appearance',
  'embed',
  'activity',
  'danger',
])

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
        <p className="py-8 text-sm text-gray-400">
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
    { id: 'details', label: m.admin_shared_tab_general(), icon: Info },
    { id: 'kbs', label: m.admin_shared_wizard_step_kb_access(), icon: Shield },
    { id: 'appearance', label: m.admin_widgets_wizard_step_appearance(), icon: Palette },
    { id: 'embed', label: m.admin_widgets_wizard_step_embed(), icon: Code2 },
    { id: 'activity', label: 'Activiteit', icon: Activity },
    { id: 'danger', label: m.admin_shared_tab_danger(), icon: AlertTriangle },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/admin/widgets/$id',
      params: { id },
      search: { tab },
    })
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-8">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {widget.name}
          </h1>
          {widget.description && (
            <p className="text-sm text-gray-400 mt-1">
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

      <div className="border-b border-gray-200">
        <nav className="-mb-px flex gap-6">
          {tabs.map(({ id: tabId, label, icon: TabIcon }) => {
            const isActive = tabId === activeTab
            return (
              <Button
                key={tabId}
                type="button"
                variant="link"
                onClick={() => setTab(tabId)}
                className={[
                  'h-auto rounded-none px-0 pb-3 text-sm font-medium no-underline border-b-2 transition-colors hover:no-underline',
                  isActive
                    ? 'border-gray-200 text-gray-900'
                    : 'border-transparent text-gray-400 hover:text-gray-900',
                ].join(' ')}
              >
                <TabIcon className="h-4 w-4" />
                {label}
              </Button>
            )
          })}
        </nav>
      </div>

      {activeTab === 'details' && <DetailsTab widget={widget} />}
      {activeTab === 'kbs' && <KnowledgeBasesTab widget={widget} />}
      {activeTab === 'appearance' && <AppearanceTab widget={widget} />}
      {activeTab === 'embed' && <EmbedTab widget={widget} />}
      {activeTab === 'activity' && <ActivityTab widget={widget} />}
      {activeTab === 'danger' && <DangerTab widget={widget} />}
    </div>
  )
}
