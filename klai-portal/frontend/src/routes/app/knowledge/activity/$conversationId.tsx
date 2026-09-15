import { createFileRoute } from '@tanstack/react-router'

/**
 * Placeholder for the conversation activity view — the real page lands in the
 * activity brief. Created so /app/knowledge/gaps can link to it and compile.
 */
export const Route = createFileRoute('/app/knowledge/activity/$conversationId')({
  component: () => <p>…</p>,
})
