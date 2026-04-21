import { createFileRoute, Outlet } from '@tanstack/react-router'

// Layout route for all /$locale/signup/* routes.
// The actual signup page lives in signup/index.tsx.
// The social signup completion form lives in signup/social.tsx.
export const Route = createFileRoute('/$locale/signup')({
  component: () => <Outlet />,
})
