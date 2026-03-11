import { createFileRoute } from '@tanstack/react-router'

const chatUrl = (() => {
  const { hostname } = window.location
  if (hostname !== 'localhost') {
    return `https://chat.${hostname}/oauth/openid`
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
