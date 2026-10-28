import { useEffect } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { AlertTriangle, BookOpen, Info, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { Tabs, type TabItem } from '@/components/ui/tabs'
import * as m from '@/paraglide/messages'
import { isSlackLockedError, useDisconnectSlack, useSlackStatus } from './-hooks'
import { SlackConnectCard } from './_components/SlackConnectCard'
import { SlackDisconnectTab } from './_components/SlackDisconnectTab'
import { SlackKbTab } from './_components/SlackKbTab'
import { SlackLogo } from './_components/SlackLogo'
import { SlackOverviewTab } from './_components/SlackOverviewTab'
import { SlackStatusAlert } from './_components/SlackStatusAlert'

type TabId = 'overview' | 'kbs' | 'disconnect'

const VALID_TABS = new Set<TabId>(['overview', 'kbs', 'disconnect'])

/** What the Slack OAuth callback appends to the page URL when it sends the admin back. */
type SlackReturn = 'connected' | 'denied' | 'failed' | 'already_linked'

const VALID_RETURNS = new Set<SlackReturn>(['connected', 'denied', 'failed', 'already_linked'])

type SlackSearch = {
  tab?: TabId
  slack?: SlackReturn
}

export const Route = createFileRoute('/admin/slack/')({
  validateSearch: (search: Record<string, unknown>): SlackSearch => ({
    tab: (VALID_TABS as Set<string>).has(search.tab as string)
      ? (search.tab as TabId)
      : undefined,
    slack: (VALID_RETURNS as Set<string>).has(search.slack as string)
      ? (search.slack as SlackReturn)
      : undefined,
  }),
  component: SlackPage,
})

function showReturnToast(result: SlackReturn) {
  const options = { id: 'slack-return' }
  if (result === 'connected') toast.success(m.admin_slack_return_connected(), options)
  else if (result === 'denied') toast.error(m.admin_slack_return_denied(), options)
  else if (result === 'failed') toast.error(m.admin_slack_return_failed(), options)
  else toast.warning(m.admin_slack_return_already_linked(), options)
}

function SlackPage() {
  const search = Route.useSearch()
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = useSlackStatus()
  const disconnectMutation = useDisconnectSlack()

  // Tell the admin how the Slack round trip ended, then drop the param so a
  // reload does not repeat the message. The round trip is a full page load,
  // so the status query is fetched fresh and needs no invalidation.
  const returned = search.slack
  const returnedTab = search.tab
  useEffect(() => {
    if (!returned) return
    showReturnToast(returned)
    void navigate({ to: '/admin/slack', search: { tab: returnedTab }, replace: true })
  }, [returned, returnedTab, navigate])

  const title = (name: string) => (
    <span className="inline-flex items-center gap-2">
      <SlackLogo className="h-6 w-6" />
      {name}
    </span>
  )

  if (isLoading) {
    return (
      <PageContainer width="4xl" gap="6">
        <PageHeader title={title(m.admin_section_slack_title())} />
        <ListLoadingState label={m.admin_slack_loading()} />
      </PageContainer>
    )
  }

  if (error) {
    return (
      <PageContainer width="4xl" gap="6">
        <PageHeader title={title(m.admin_section_slack_title())} />
        {isSlackLockedError(error) ? (
          <ListEmptyState icon={AlertTriangle} title={m.admin_slack_unlock_required()} />
        ) : (
          <QueryErrorState
            error={error instanceof Error ? error : new Error(String(error))}
            onRetry={() => void refetch()}
          />
        )}
      </PageContainer>
    )
  }

  if (!data) return null

  const connected = data.status === 'connected'

  const tabs: TabItem<TabId>[] = [
    { id: 'overview', label: m.admin_slack_tab_overview(), icon: Info },
    { id: 'kbs', label: m.admin_slack_tab_kbs(), icon: BookOpen },
    { id: 'disconnect', label: m.admin_slack_tab_disconnect(), icon: Trash2 },
  ]
  const activeTab: TabId = search.tab ?? 'overview'

  function setTab(tab: TabId) {
    void navigate({ to: '/admin/slack', search: { tab } })
  }

  const badge = connected ? (
    <Badge variant="success">{m.admin_slack_status_connected()}</Badge>
  ) : data.status === 'revoked' ? (
    <Badge variant="warning">{m.admin_slack_status_revoked()}</Badge>
  ) : null

  const pageTitle = title(
    connected && data.installation ? data.installation.team_name : m.admin_section_slack_title(),
  )

  return (
    <PageContainer width="4xl" gap="6">
      <PageHeader
        title={pageTitle}
        description={m.admin_section_slack_description()}
        actions={badge}
      />

      {data.status === 'not_connected' && <SlackConnectCard />}

      {data.status === 'revoked' && (
        <>
          <SlackStatusAlert
            isReconnecting={disconnectMutation.isPending}
            onReconnect={() => disconnectMutation.mutate()}
          />
          {disconnectMutation.error && (
            <p className="text-sm text-[var(--color-destructive)]">
              {disconnectMutation.error.message}
            </p>
          )}
        </>
      )}

      {connected && data.installation && (
        <>
          <Tabs tabs={tabs} value={activeTab} onValueChange={setTab} />
          {activeTab === 'overview' && <SlackOverviewTab installation={data.installation} />}
          {activeTab === 'kbs' && (
            <SlackKbTab key={[...data.kb_ids].sort().join(',')} kbIds={data.kb_ids} />
          )}
          {activeTab === 'disconnect' && <SlackDisconnectTab />}
        </>
      )}
    </PageContainer>
  )
}
