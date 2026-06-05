import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import * as m from '@/paraglide/messages'
import { usePlatformOrgDetail } from './-hooks'
import {
  BotsSection,
  KnowledgeBasesSection,
  OrgSummaryStats,
  TemplatesSection,
  TenantDangerZone,
  UsersSection,
} from './-components/OrgDetailSections'

export const Route = createFileRoute('/admin/platform/orgs/$orgId')({
  component: PlatformOrgDetailPage,
})

function fmtDate(iso: string | null): string {
  if (!iso) return '-'
  return datetime(getLocale(), iso, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function PlatformOrgDetailPage() {
  const { orgId } = useParams({ from: '/admin/platform/orgs/$orgId' })
  const navigate = useNavigate()
  const { data, isLoading, error, refetch } = usePlatformOrgDetail(orgId)

  return (
    <div className="mx-auto max-w-5xl px-6 pt-4 pb-10 space-y-8">
      <Button
        type="button"
        variant="link"
        onClick={() => void navigate({ to: '/admin/platform' })}
        className="h-auto justify-start p-0 text-sm font-medium text-gray-500 no-underline hover:text-gray-900 hover:no-underline"
      >
        <ArrowLeft className="h-4 w-4" />
        {m.platform_back_to_platform()}
      </Button>

      {isLoading && (
        <p className="py-8 text-sm text-gray-400">
          <Loader2 className="inline h-4 w-4 animate-spin mr-2" />
          {m.admin_shared_loading()}
        </p>
      )}

      {error && (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(String(error))}
          onRetry={() => void refetch()}
        />
      )}

      {data && (
        <>
          <div>
            <h1 className="page-title text-[26px] font-display-bold text-gray-900">
              {data.org.name}
            </h1>
            <div className="mt-2 flex items-center gap-2 flex-wrap text-sm text-gray-400">
              <span className="font-mono">{data.org.slug}</span>
              <span>·</span>
              <Badge variant="outline">{data.org.plan}</Badge>
              <Badge
                variant={
                  data.org.provisioning_status === 'ready'
                    ? 'success'
                    : 'outline'
                }
              >
                {data.org.provisioning_status}
              </Badge>
              <span>·</span>
              <span>
                {m.platform_created_at({ date: fmtDate(data.org.created_at) })}
              </span>
            </div>
          </div>

          <OrgSummaryStats
            org={data.org}
            templateCount={data.templates.length}
          />
          <UsersSection orgId={orgId} users={data.users} />
          <BotsSection bots={data.bots} fmtDate={fmtDate} />
          <KnowledgeBasesSection
            knowledgeBases={data.knowledge_bases}
            fmtDate={fmtDate}
          />
          <TemplatesSection templates={data.templates} fmtDate={fmtDate} />
          <TenantDangerZone org={data.org} />
        </>
      )}
    </div>
  )
}
