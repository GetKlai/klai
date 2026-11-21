/**
 * Old address of a weekly-list group: the group page lives directly under the
 * Knowledge gaps screen now, with the same key.
 */
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/knowledge/gaps/clusters/$clusterKey')({
  beforeLoad: ({ params }) => {
    throw redirect({ to: '/app/knowledge/gaps/$clusterKey', params: { clusterKey: params.clusterKey } })
  },
  // beforeLoad always throws, but TanStack Router requires a component field.
  component: () => null,
})
