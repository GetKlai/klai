/**
 * Focus redirect — SPEC-PORTAL-UNIFY-KB-001 D5
 *
 * All /app/focus/* routes are redirected to /app/knowledge.
 * Focus has been collapsed into Knowledge; this stub prevents 404s for
 * bookmarks / internal testers.
 *
 * TanStack Router's beforeLoad runs before any component renders, so the
 * redirect is synchronous and leaves no flash.
 */
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/focus')({
  beforeLoad() {
    throw redirect({ to: '/app/knowledge' })
  },
  // The component is never rendered because beforeLoad always throws, but
  // TanStack Router requires a component field.
  component: () => null,
})
