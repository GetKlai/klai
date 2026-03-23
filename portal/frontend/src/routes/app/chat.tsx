import { createFileRoute } from '@tanstack/react-router'

const chatUrl = (() => {
  const { hostname } = window.location
  if (hostname !== 'localhost') {
    // Portal runs at {tenant}.getklai.com — extract tenant slug and root domain
    // to build chat-{tenant}.getklai.com (single-level subdomain, covered by wildcard cert)
    const [tenant, ...rest] = hostname.split('.')
    return `https://chat-${tenant}.${rest.join('.')}/oauth/openid`
  }
  return 'http://localhost:3080'
})()

export const Route = createFileRoute('/app/chat')({
  component: ChatPage,
})

function ChatPage() {
  return (
    <div className="h-full w-full" data-help-id="chat-page">
      <iframe
        src={chatUrl}
        className="h-full w-full border-none"
        title="Chat"
        allow="clipboard-write; microphone"
      />
    </div>
  )
}
