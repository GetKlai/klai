import { createFileRoute, redirect } from '@tanstack/react-router'

// "Templates" is hernoemd naar "Instructies" (Phase 1 van de rename).
// Deze route houdt /app/templates levend voor oude bookmarks en
// email-links en redirect naar de nieuwe canonical URL /app/instructions.
export const Route = createFileRoute('/app/templates/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/instructions' })
  },
})
