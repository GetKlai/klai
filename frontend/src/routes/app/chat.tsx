import { createFileRoute } from '@tanstack/react-router'

const chatUrl = (() => {
  const parts = window.location.hostname.split('.')
  if (parts.length >= 3) {
    return `https://chat.${window.location.hostname}`
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
