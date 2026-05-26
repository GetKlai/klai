import { createFileRoute, redirect } from '@tanstack/react-router'

// "Templates" is hernoemd naar "Instructies" (Phase 1 van de rename).
// Deze route houdt /admin/templates levend voor oude bookmarks en
// redirect naar /app/instructions (de admin variant is samengevoegd in /app).
export const Route = createFileRoute('/admin/templates/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/instructions' })
  },
})
