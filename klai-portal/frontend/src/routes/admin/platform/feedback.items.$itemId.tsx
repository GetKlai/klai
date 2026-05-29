import { createFileRoute, useNavigate, useParams } from '@tanstack/react-router'
import { getLocale } from '@/paraglide/runtime'
import { datetime } from '@/paraglide/registry'
import { FeedbackItemDetailPanel } from './-components/PlatformDashboardTabs'

export const Route = createFileRoute('/admin/platform/feedback/items/$itemId')({
  component: PlatformFeedbackItemPage,
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

function PlatformFeedbackItemPage() {
  const { itemId } = useParams({ from: '/admin/platform/feedback/items/$itemId' })
  const navigate = useNavigate()
  const id = Number(itemId)
  const backToPlatform = () =>
    void navigate({ to: '/admin/platform', search: { tab: 'feedback' } })

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-6 py-10">
      <FeedbackItemDetailPanel
        itemId={Number.isFinite(id) ? id : -1}
        fmtDate={fmtDate}
        onClose={backToPlatform}
      />
    </div>
  )
}
