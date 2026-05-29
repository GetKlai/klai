import { useNavigate } from "@tanstack/react-router"
import { useState } from "react"
import { ChevronRight, Loader2 } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Select } from "@/components/ui/select"
import * as m from "@/paraglide/messages"
import { usePlatformFeedbackItems, usePlatformFeedbackSubmissions } from "../../-hooks"
import type { PlatformFeedbackSubmission } from "../../-types"
import { PlatformTableShell, TD, TH } from "../PlatformShell"
import {
  feedbackItemKindLabel,
  feedbackItemReporterSummary,
  feedbackItemStatusLabel,
  feedbackKindLabel,
  feedbackStatusLabel,
  feedbackSubmissionReporterLabel,
} from "./-feedback-helpers"

export function FeedbackTab({
  search,
  status,
  kind,
  fmtDate,
}: {
  search: string
  status: string
  kind: string
  fmtDate: (s: string | null) => string
}) {
  const { data, isLoading } = usePlatformFeedbackSubmissions(search, status, kind)
  const navigate = useNavigate()
  const [feedbackView, setFeedbackView] = useState<'inbox' | 'items'>('inbox')
  const rows = data ?? []

  return (
    <>
      <div className="mb-5 inline-flex rounded-lg border border-gray-200 bg-white p-1">
        <Button
          type="button"
          size="sm"
          variant={feedbackView === 'inbox' ? 'default' : 'ghost'}
          onClick={() => setFeedbackView('inbox')}
        >
          {m.platform_feedback_view_inbox()}
        </Button>
        <Button
          type="button"
          size="sm"
          variant={feedbackView === 'items' ? 'default' : 'ghost'}
          onClick={() => setFeedbackView('items')}
        >
          {m.platform_feedback_view_items()}
        </Button>
      </div>

      {feedbackView === 'items' ? (
        <OpenItemsPanel
          search={search}
          fmtDate={fmtDate}
          onOpenItem={(itemId) =>
            void navigate({
              to: '/admin/platform/feedback/items/$itemId',
              params: { itemId: String(itemId) },
            })
          }
        />
      ) : (
        <PlatformTableShell
          loading={isLoading}
          empty={rows.length === 0}
          emptyText={m.platform_empty_feedback()}
        >
          <thead>
            <tr className="border-b border-gray-200">
              <th className={TH}>{m.platform_col_type()}</th>
              <th className={TH}>{m.platform_col_status()}</th>
              <th className={TH}>{m.platform_col_organization()}</th>
              <th className={TH}>{m.platform_col_detail()}</th>
              <th className={TH}>{m.platform_col_time()}</th>
              <th className={TH}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <FeedbackSubmissionRow
                key={item.id}
                item={item}
                fmtDate={fmtDate}
                onOpen={() =>
                  void navigate({
                    to: '/admin/platform/feedback/submissions/$submissionId',
                    params: { submissionId: String(item.id) },
                  })
                }
              />
            ))}
          </tbody>
        </PlatformTableShell>
      )}
    </>
  )
}

function FeedbackSubmissionRow({
  item,
  fmtDate,
  onOpen,
}: {
  item: PlatformFeedbackSubmission
  fmtDate: (s: string | null) => string
  onOpen: () => void
}) {
  return (
    <tr
      className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter') onOpen()
      }}
    >
      <td className={TD}>
        <div className="flex flex-col items-start gap-2">
          <Badge
            variant={
              item.event_type === 'klai_assistant.problem_report'
                ? 'destructive'
                : item.event_type === 'klai_assistant.feedback'
                  ? 'success'
                  : 'outline'
            }
          >
            {feedbackKindLabel(item.event_type)}
          </Badge>
          {(item.feedback_type || item.severity) && (
            <span className="text-xs text-gray-400">
              {item.feedback_type || item.severity}
            </span>
          )}
        </div>
      </td>
      <td className={TD}>
        <Badge variant={item.status === 'new' ? 'outline' : 'secondary'}>
          {feedbackStatusLabel(item.status)}
        </Badge>
      </td>
      <td className={TD}>
        <span className="font-medium">
          {item.org_name ?? (item.org_id ? `#${item.org_id}` : '-')}
        </span>
        {item.org_slug && (
          <p className="font-mono text-xs text-gray-400">{item.org_slug}</p>
        )}
        {feedbackSubmissionReporterLabel(item) && (
          <p className="mt-1 max-w-[180px] truncate text-xs text-gray-400">
            {feedbackSubmissionReporterLabel(item)}
          </p>
        )}
      </td>
      <td className={`${TD} max-w-md`}>
        <p className="line-clamp-3 whitespace-pre-wrap text-sm leading-6">
          {item.raw_text ?? '-'}
        </p>
      </td>
      <td className={`${TD} whitespace-nowrap tabular-nums text-gray-400`}>
        {fmtDate(item.created_at)}
      </td>
      <td className={`${TD} text-right`}>
        <ChevronRight className="ml-auto h-4 w-4 text-gray-300" />
      </td>
    </tr>
  )
}

function OpenItemsPanel({
  search,
  fmtDate,
  onOpenItem,
}: {
  search: string
  fmtDate: (s: string | null) => string
  onOpenItem: (itemId: number) => void
}) {
  const [statusFilter, setStatusFilter] = useState('active')
  const [kindFilter, setKindFilter] = useState('all')
  const items = usePlatformFeedbackItems(search, statusFilter, kindFilter)
  const rows = items.data ?? []

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value)}
            className="h-9 min-w-[150px]"
            aria-label={m.platform_feedback_filter_status()}
          >
            <option value="active">{m.platform_feedback_filter_active()}</option>
            <option value="open">{m.platform_feedback_status_open()}</option>
            <option value="resolved">{m.platform_feedback_status_resolved()}</option>
            <option value="dismissed">{m.platform_feedback_status_dismissed()}</option>
            <option value="all">{m.platform_feedback_filter_all()}</option>
          </Select>
          <Select
            value={kindFilter}
            onChange={(event) => setKindFilter(event.target.value)}
            className="h-9 min-w-[130px]"
            aria-label={m.platform_feedback_filter_type()}
          >
            <option value="all">{m.platform_feedback_filter_all_types()}</option>
            <option value="bug">{m.platform_feedback_item_kind_bug()}</option>
            <option value="feature">{m.platform_feedback_item_kind_feature()}</option>
            <option value="ux_confusion">{m.platform_feedback_item_kind_ux()}</option>
            <option value="docs">{m.platform_feedback_item_kind_docs()}</option>
            <option value="support_pattern">{m.platform_feedback_item_kind_support()}</option>
          </Select>
        </div>
        <p className="text-xs text-gray-400">
          {m.platform_feedback_closed_hidden_hint()}
        </p>
      </div>
      {items.isFetching && !items.isLoading && (
        <p className="mb-2 text-xs text-gray-400">
          <Loader2 className="mr-2 inline h-3 w-3 animate-spin" />
          {m.platform_feedback_items_refreshing()}
        </p>
      )}
      <PlatformTableShell
        loading={items.isLoading}
        empty={rows.length === 0}
        emptyText={m.platform_feedback_items_empty()}
      >
          <thead>
            <tr className="border-b border-gray-200">
              <th className={TH}>{m.platform_feedback_col_item()}</th>
              <th className={TH}>{m.platform_feedback_col_organizations()}</th>
              <th className={TH}>{m.platform_col_status()}</th>
              <th className={TH}>{m.platform_col_type()}</th>
              <th className={TH}>{m.platform_feedback_col_updated()}</th>
              <th className={TH}></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {rows.map((item) => (
                <tr
                  key={item.id}
                  className="cursor-pointer border-b border-gray-200 last:border-b-0 klai-hover"
                  tabIndex={0}
                  onClick={() => onOpenItem(item.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') onOpenItem(item.id)
                  }}
                >
                  <td className={`${TD} max-w-xl`}>
                    <span className="block truncate font-medium text-gray-900">
                      {item.title}
                    </span>
                    <span className="mt-1 block truncate text-xs text-gray-400">
                      {[item.area, item.owner && m.platform_feedback_owner({ owner: item.owner })]
                        .filter(Boolean)
                        .join(' / ')}
                    </span>
                  </td>
                  <td className={`${TD} max-w-xs`}>
                    <span className="block truncate text-sm text-gray-900">
                      {feedbackItemReporterSummary(item)}
                    </span>
                    <span className="mt-1 block text-xs text-gray-400">
                      {m.platform_feedback_reporter_counts({
                        orgs: item.org_count,
                        users: item.user_count,
                      })}
                    </span>
                  </td>
                  <td className={TD}>
                    <Badge variant="outline">{feedbackItemStatusLabel(item.status)}</Badge>
                  </td>
                  <td className={TD}>{feedbackItemKindLabel(item.kind)}</td>
                  <td className={`${TD} whitespace-nowrap text-gray-400`}>
                    {fmtDate(item.updated_at)}
                  </td>
                  <td className={`${TD} text-right`}>
                    <ChevronRight className="ml-auto h-4 w-4 text-gray-300" />
                  </td>
                </tr>
              ))}
          </tbody>
      </PlatformTableShell>
    </>
  )
}
