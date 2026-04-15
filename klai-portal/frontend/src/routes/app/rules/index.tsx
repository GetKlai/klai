import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/rules/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/templates' })
  },
})
