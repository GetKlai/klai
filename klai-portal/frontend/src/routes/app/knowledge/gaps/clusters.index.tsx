/**
 * Old address of the weekly list: it is the Knowledge gaps screen now
 * (/app/knowledge/gaps), so a bookmark lands there.
 */
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/knowledge/gaps/clusters/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/knowledge/gaps' })
  },
  // beforeLoad always throws, but TanStack Router requires a component field.
  component: () => null,
})
