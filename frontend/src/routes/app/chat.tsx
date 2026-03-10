import { createFileRoute } from '@tanstack/react-router'

// Point directly to the OIDC endpoint so auth is triggered automatically.
// If LibreChat already has a session it redirects to /; if not it completes
// the OIDC flow via Zitadel (which auto-authenticates the already-logged-in user).
const chatUrl = (() => {
  const parts = window.location.hostname.split('.')
  if (parts.length >= 3) {
    return `https://chat.${window.location.hostname}/oauth/openid`
  }
  return 'http://localhost:3080'
})()

export const Route = createFileRoute('/app/chat')({
  component: ChatPage,
})

function ChatPage() {
  return (
    <iframe
      src={chatUrl}
      className="h-full w-full border-none"
      title="Chat"
      allow="clipboard-write; microphone"
    />
  )
}
