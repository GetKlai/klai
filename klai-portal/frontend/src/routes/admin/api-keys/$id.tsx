import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ArrowLeft, Info, Shield, Settings, AlertTriangle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { useApiKey } from './-hooks'
import { DetailsTab } from './_components/tabs/DetailsTab'
import { PermissionsTab } from './_components/tabs/PermissionsTab'
import { KnowledgeBasesTab } from './_components/tabs/KnowledgeBasesTab'
import { RateLimitTab } from './_components/tabs/RateLimitTab'
import { DangerTab } from './_components/tabs/DangerTab'

type TabId = 'details' | 'permissions' | 'kbs' | 'rate_limit' | 'danger'

const VALID_TABS = new Set<TabId>(['details', 'permissions', 'kbs', 'rate_limit', 'danger'])

type DetailSearch = {
  tab?: TabId
}

export const Route = createFileRoute('/admin/api-keys/$id')({
  validateSearch: (search: Record<string, unknown>): DetailSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
  }),
  component: ApiKeyDetailPage,
})

function ApiKeyDetailPage() {
  const { id } = Route.useParams()
  const search = Route.useSearch()
  const navigate = useNavigate()

  const { data: apiKey, isLoading, error, refetch } = useApiKey(id)

  const activeTab: TabId = search.tab ?? 'details'

  if (isLoading) {
    return (
      <div className="p-6">
        <p className="py-8 text-sm text-[var(--color-muted-foreground)]">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_api_keys_loading()}
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

  if (!apiKey) return null

  const tabs: { id: TabId; label: string; icon: React.ElementType }[] = [
    { id: 'details', label: m.admin_shared_tab_general(), icon: Info },
    { id: 'permissions', label: m.admin_api_keys_wizard_step_permissions(), icon: Shield },
    { id: 'kbs', label: m.admin_shared_wizard_step_kb_access(), icon: Shield },
    { id: 'rate_limit', label: m.admin_api_keys_wizard_step_rate_limit(), icon: Settings },
    { id: 'danger', label: m.admin_shared_tab_danger(), icon: AlertTriangle },
  ]

  function setTab(tab: TabId) {
    void navigate({
      to: '/admin/api-keys/$id',
      params: { id },
      search: { tab },
    })
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10 space-y-8">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {apiKey.name}
          </h1>
          {apiKey.description && (
            <p className="text-sm text-[var(--color-muted-foreground)] mt-1">
              {apiKey.description}
            </p>
          )}
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => navigate({ to: '/admin/api-keys' })}
        >
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.admin_api_keys_back_to_list()}
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

      {activeTab === 'details' && <DetailsTab apiKey={apiKey} />}
      {activeTab === 'permissions' && <PermissionsTab apiKey={apiKey} />}
      {activeTab === 'kbs' && <KnowledgeBasesTab apiKey={apiKey} />}
      {activeTab === 'rate_limit' && <RateLimitTab apiKey={apiKey} />}
      {activeTab === 'danger' && <DangerTab apiKey={apiKey} />}
    </div>
  )
}
