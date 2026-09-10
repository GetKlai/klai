import { useState } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import {
  ArrowLeft,
  Info,
  Shield,
  Palette,
  Code2,
  PanelRightOpen,
  Plug,
  Activity,
  AlertTriangle,
  Loader2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { useAuth } from '@/lib/auth'
import { STORAGE_KEYS } from '@/lib/storage'
import * as m from '@/paraglide/messages'
import { useWidget } from './-hooks'
import { WidgetPreviewProvider } from './-preview'
import { DetailsTab } from './_components/tabs/DetailsTab'
import { KnowledgeBasesTab } from './_components/tabs/KnowledgeBasesTab'
import { AppearanceTab } from './_components/tabs/AppearanceTab'
import { EmbedTab } from './_components/tabs/EmbedTab'
import { IntegrationsTab } from './_components/tabs/IntegrationsTab'
import { ActivityTab } from './_components/tabs/ActivityTab'
import { DangerTab } from './_components/tabs/DangerTab'
import { WidgetPreviewPanel } from './_components/WidgetPreviewPanel'
import { PageContainer } from '@/components/ui/page-container'

type TabId = 'details' | 'kbs' | 'appearance' | 'embed' | 'integrations' | 'activity' | 'danger'

const VALID_TABS = new Set<TabId>([
  'details',
  'kbs',
  'appearance',
  'embed',
  'integrations',
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
  const auth = useAuth()

  const { data: widget, isLoading, error, refetch } = useWidget(id)

  // The preview panel is collapsed per admin (localStorage is keyed on the
  // user id, so a shared browser still remembers each admin's choice) and
  // open by default.
  const previewStorageKey = STORAGE_KEYS.widgetsPreviewCollapsed +
    (auth.user ? `:${auth.user.profile.sub}` : '')
  const [previewOpen, setPreviewOpen] = useState(() => {
    try {
      return localStorage.getItem(previewStorageKey) !== 'true'
    } catch {
      return true
    }
  })

  function setPreviewVisibility(open: boolean) {
    setPreviewOpen(open)
    try {
      localStorage.setItem(previewStorageKey, String(!open))
    } catch { /* localStorage unavailable in sandboxed contexts */ }
  }

  // The integrations tab used to be visible only on one hardcoded hostname,
  // from when the HubSpot handoff was a single-tenant pilot. That hid a fully
  // built feature from every other tenant while the backend was ready to serve
  // it. Availability is now the widget's own integration state, which the tab
  // itself already renders.
  const requestedTab: TabId = search.tab ?? 'details'
  const activeTab: TabId = requestedTab

  if (isLoading) {
    return (
      <div className="p-6">
        <p className="py-8 text-sm text-gray-600">
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

  const tabs: TabItem<TabId>[] = [
    { id: 'details', label: m.admin_shared_tab_general(), icon: Info },
    { id: 'kbs', label: m.admin_shared_wizard_step_kb_access(), icon: Shield },
    { id: 'appearance', label: m.admin_widgets_wizard_step_appearance(), icon: Palette },
    { id: 'embed', label: m.admin_widgets_wizard_step_embed(), icon: Code2 },
    { id: 'integrations' as const, label: m.admin_widgets_integrations_tab(), icon: Plug },
    { id: 'activity', label: m.admin_widgets_tab_activity(), icon: Activity },
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
    <PageContainer width={previewOpen ? '6xl' : '4xl'} gap="8">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-display-bold text-gray-900">
            {widget.name}
          </h1>
          {widget.description && (
            <p className="text-sm text-gray-600 mt-1">
              {widget.description}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!previewOpen && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setPreviewVisibility(true)}
            >
              <PanelRightOpen className="h-4 w-4 mr-2" />
              {m.admin_widgets_preview_open()}
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => navigate({ to: '/admin/widgets' })}
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.admin_widgets_back_to_list()}
          </Button>
        </div>
      </div>

      {/* Settings on the left, live visitor preview on the right; the panel
          stacks below the content on narrow screens. */}
      <WidgetPreviewProvider widget={widget}>
        <div className={`grid min-w-0 items-start gap-8 ${previewOpen ? 'xl:grid-cols-[minmax(0,1fr)_24rem]' : ''}`}>
          <div className="min-w-0 space-y-8">
            <Tabs
              tabs={tabs}
              value={activeTab}
              onValueChange={setTab}
              className="overflow-x-auto"
            />

            {activeTab === 'details' && <DetailsTab widget={widget} />}
            {activeTab === 'kbs' && <KnowledgeBasesTab widget={widget} />}
            {activeTab === 'appearance' && <AppearanceTab widget={widget} />}
            {activeTab === 'embed' && <EmbedTab widget={widget} />}
            {activeTab === 'integrations' && (
              <IntegrationsTab widget={widget} />
            )}
            {activeTab === 'activity' && <ActivityTab widget={widget} />}
            {activeTab === 'danger' && <DangerTab widget={widget} />}
          </div>

          {previewOpen && (
            <WidgetPreviewPanel
              widget={widget}
              onCollapse={() => setPreviewVisibility(false)}
            />
          )}
        </div>
      </WidgetPreviewProvider>
    </PageContainer>
  )
}
