/**
 * Focus sub-path catch-all redirect — SPEC-PORTAL-UNIFY-KB-001 R-E4
 *
 * Catches all /app/focus/* sub-paths (e.g. /app/focus/new,
 * /app/focus/<notebook-id>, /app/focus/<id>/edit) and redirects to
 * /app/knowledge.
 *
 * The parent route (focus.tsx) handles the exact /app/focus path.
 * This splat route handles everything below it.
 *
 * TanStack Router file-based routing: $.tsx is the catch-all (splat) segment.
 */
import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/app/focus/$')({
  beforeLoad() {
    throw redirect({ to: '/app/knowledge' })
  },
  // The component is never rendered because beforeLoad always throws, but
  // TanStack Router requires a component field.
  component: () => null,
})
