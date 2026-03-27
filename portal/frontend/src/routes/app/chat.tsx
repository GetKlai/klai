import { createFileRoute } from '@tanstack/react-router'

import { KBScopeBar } from './_components/KBScopeBar'

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
    <div className="flex h-full w-full flex-col" data-help-id="chat-page">
      <KBScopeBar />
      <iframe
        src={chatUrl}
        className="w-full flex-1 border-none"
        title="Chat"
        allow="clipboard-write; microphone; screen-wake-lock"
      />
    </div>
  )
}
