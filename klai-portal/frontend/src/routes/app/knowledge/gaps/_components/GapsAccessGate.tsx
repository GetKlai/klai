// Same capability and unlock rules as the gap inbox (kb.gaps capability plus the
// tenant's knowledge_gaps unlock), shared by the cluster screens so neither
// fetches anything before both hold.
import type { ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { PageContainer } from '@/components/ui/page-container'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { Tooltip } from '@/components/ui/tooltip'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { fetchMe } from '@/lib/api-me'
import * as m from '@/paraglide/messages'
import { appNavGapsIsVisible } from '@/routes/app/-app-tools'

export function GapsAccessGate({ width, children }: { width: '3xl' | '6xl'; children: ReactNode }) {
  const { user } = useCurrentUser()
  const hasGapsCapability = user?.hasCapability('kb.gaps') === true
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: hasGapsCapability,
  })
  const me = meQuery.data

  if (!hasGapsCapability) {
    return (
      <div className="p-6 max-w-2xl opacity-50 cursor-default select-none" aria-disabled="true">
        <div className="flex items-start gap-3 mb-4">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="text-xl/none font-semibold text-gray-900">{m.gaps_page_title()}</h1>
        </div>
        <Tooltip label={m.capability_tooltip_knowledge_only()}>
          <p className="text-sm text-gray-600">{m.capability_tooltip_knowledge_only()}</p>
        </Tooltip>
      </div>
    )
  }
  if (meQuery.isLoading) {
    return (
      <PageContainer width={width} gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }
  if (meQuery.isError) {
    return (
      <PageContainer width={width} gap="6">
        <QueryErrorState error={meQuery.error} onRetry={() => void meQuery.refetch()} />
      </PageContainer>
    )
  }
  const unlocked = appNavGapsIsVisible({
    hasCapability: (cap) => user?.hasCapability(cap) === true,
    unlockedFeatures: me?.platform_unlocked_features ?? [],
  })
  if (!unlocked) {
    return (
      <PageContainer width={width} gap="6">
        <ListEmptyState icon={AlertTriangle} title={m.gaps_unlock_required()} />
      </PageContainer>
    )
  }
  return <>{children}</>
}
