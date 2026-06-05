import { createFileRoute } from '@tanstack/react-router'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { ListEmptyState } from '@/components/ui/list-state'
import { ListFrame } from '@/components/ui/list'
import { PageHeader } from '@/components/ui/page-header'
import * as m from '@/paraglide/messages'
import { TaxonomyTab } from './_components/TaxonomyTab'
import { KBOverviewSections } from './_components/KBOverviewSections'

export const Route = createFileRoute('/app/knowledge/$kbSlug/insights')({
  component: () => (
    <RoleGuard minRole="kb_manager">
      <InsightsTab />
    </RoleGuard>
  ),
})

function InsightsTab() {
  const { kbSlug } = Route.useParams()
  return (
    <div className="space-y-8">
      <PageHeader title={m.kb_tab_insights()} />

      {/* Docs + Statistieken (moved from the retired /overview route). */}
      <KBOverviewSections kbSlug={kbSlug} />

      <section className="border-t border-gray-200 pt-8">
        <TaxonomyTab kbSlug={kbSlug} />
      </section>

      <section className="border-t border-gray-200 pt-8">
        <ListFrame>
          <ListEmptyState
            title={m.knowledge_insights_sync_history_title()}
            description={m.knowledge_insights_sync_history_description()}
          />
        </ListFrame>
      </section>
    </div>
  )
}
