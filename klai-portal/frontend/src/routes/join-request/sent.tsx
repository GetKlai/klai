import { createFileRoute } from '@tanstack/react-router'
import { CheckCircle2 } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { useLocale } from '@/lib/locale'
import { AuthPageLayout } from '@/components/layout/AuthPageLayout'

export const Route = createFileRoute('/join-request/sent')({
  component: JoinRequestSentPage,
})

function JoinRequestSentPage() {
  useLocale()

  const leftContent = (
    <>
      <h1 className="text-2xl font-semibold leading-tight">
        {m.no_account_hero_heading()}
        <br />
        <span className="text-[var(--color-rl-accent)]">{m.no_account_hero_highlight()}</span>
      </h1>
      <p className="text-base leading-relaxed text-[var(--color-rl-cream)]">
        {m.no_account_hero_body()}
      </p>
    </>
  )

  return (
    <AuthPageLayout leftContent={leftContent} showLocale>
      <div className="flex flex-col items-center gap-4 text-center">
        <CheckCircle2 className="h-10 w-10 text-[var(--color-success)]" />
        <p className="text-sm text-gray-400">
          {m.join_request_success()}
        </p>
      </div>
    </AuthPageLayout>
  )
}
