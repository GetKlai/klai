/**
 * Legacy gaps route — the page moved under the knowledge section
 * (SPEC-KNOWLEDGE-ACTIVITY-001 §4.5). Search is preserved so a bookmarked
 * /app/gaps?days=14 lands on the same filtered view at its new path.
 */
import { createFileRoute, redirect } from '@tanstack/react-router'

type LegacyGapsSearch = { days?: number; gapType?: string }
const VALID_DAYS = new Set([7, 14, 30, 60, 90])

export const Route = createFileRoute('/app/gaps/')({
  validateSearch: (search: Record<string, unknown>): LegacyGapsSearch => ({
    days: VALID_DAYS.has(Number(search.days)) ? Number(search.days) : undefined,
    gapType: search.gapType === 'hard' || search.gapType === 'soft' ? (search.gapType as string) : undefined,
  }),
  beforeLoad: ({ search }) => {
    throw redirect({ to: '/app/knowledge/gaps', search })
  },
  // beforeLoad always throws, but TanStack Router requires a component field.
  component: () => null,
})
