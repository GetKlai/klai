/**
 * Legacy gaps route: the page moved under the knowledge section
 * (SPEC-KNOWLEDGE-ACTIVITY-001 §4.5) and is the grouped Knowledge gaps list
 * now. Its old filters (?days=, ?gapType=) belong to the retired inbox and are
 * dropped.
 */
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/gaps/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/knowledge/gaps' })
  },
  // beforeLoad always throws, but TanStack Router requires a component field.
  component: () => null,
})
