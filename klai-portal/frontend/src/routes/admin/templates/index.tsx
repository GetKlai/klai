import { createFileRoute, redirect } from '@tanstack/react-router'

// Templates is samengevoegd met /app/templates - één canonical surface
// met scope-tabs en per-rij edit-rechten (canMutate gate; admins kunnen
// org-wide, iedereen kan persoonlijk). Deze route blijft bestaan voor
// oude bookmarks en email-links en redirect naar de /app variant.
export const Route = createFileRoute('/admin/templates/')({
  beforeLoad: () => {
    throw redirect({ to: '/app/templates' })
  },
})
