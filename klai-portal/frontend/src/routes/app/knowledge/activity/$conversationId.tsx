import { createFileRoute } from '@tanstack/react-router'

// SPEC-KNOWLEDGE-ACTIVITY-001 §4.3: the conversation detail lives on its own
// route (part b2 fills it). It is declared here already so the activity list
// can link to it through a typed route instead of a string path.
export const Route = createFileRoute('/app/knowledge/activity/$conversationId')({
  component: () => <p className="px-6 py-10 text-sm text-gray-600">…</p>,
})
