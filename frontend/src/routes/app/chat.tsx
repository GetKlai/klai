import { createFileRoute } from '@tanstack/react-router'

const chatUrl = (() => {
  const parts = window.location.hostname.split('.')
  if (parts.length >= 3) {
    const baseDomain = parts.slice(-2).join('.')
    return `https://chat.${baseDomain}/oauth/openid`
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
