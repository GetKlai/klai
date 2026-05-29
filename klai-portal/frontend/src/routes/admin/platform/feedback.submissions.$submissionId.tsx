import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import * as m from '@/paraglide/messages'
import { usePlatformFeedbackSubmission } from './-hooks'
import { FeedbackSubmissionDetailPanel } from './-components/PlatformDashboardTabs'

export const Route = createFileRoute('/admin/platform/feedback/submissions/$submissionId')({
  component: PlatformFeedbackSubmissionPage,
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

function PlatformFeedbackSubmissionPage() {
  const { submissionId } = useParams({
    from: '/admin/platform/feedback/submissions/$submissionId',
  })
  const navigate = useNavigate()
  const id = Number(submissionId)
  const detail = usePlatformFeedbackSubmission(Number.isFinite(id) ? id : null)

  const backToPlatform = () => void navigate({ to: '/admin/platform' })

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-6 py-10">
      <Button
        type="button"
        variant="link"
        onClick={backToPlatform}
        className="h-auto justify-start p-0 text-sm font-medium text-gray-500 no-underline hover:text-gray-900 hover:no-underline"
      >
        <ArrowLeft className="h-4 w-4" />
        {m.platform_back_to_platform()}
      </Button>

      {detail.isLoading && (
        <p className="py-8 text-sm text-gray-400">
          <Loader2 className="mr-2 inline h-4 w-4 animate-spin" />
          {m.admin_shared_loading()}
        </p>
      )}

      {detail.error && (
        <QueryErrorState
          error={detail.error instanceof Error ? detail.error : new Error(String(detail.error))}
          onRetry={() => void detail.refetch()}
        />
      )}

      {detail.data && (
        <FeedbackSubmissionDetailPanel
          item={detail.data}
          fmtDate={fmtDate}
          onClose={backToPlatform}
        />
      )}
    </div>
  )
}
