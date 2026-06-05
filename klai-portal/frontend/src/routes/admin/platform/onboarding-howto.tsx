import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/admin/platform/onboarding-howto')({
  component: OnboardingHowtoPage,
})

interface StepItem {
  title: string
  body: string
  expect: string
}

function Step({
  n,
  title,
  body,
  expect,
}: {
  n: number
} & StepItem) {
  return (
    <li className="relative rounded-xl border border-gray-200 bg-white py-5 pl-16 pr-5">
      <span
        className="absolute left-4 top-5 flex h-8 w-8 items-center justify-center rounded-full bg-[var(--color-rl-accent)] font-display-bold text-sm text-[var(--color-rl-dark)]"
        aria-hidden
      >
        {n}
      </span>
      <h3 className="text-[17px] font-display text-gray-900">{title}</h3>
      <p className="mt-1.5 text-sm leading-relaxed text-gray-700">{body}</p>
      <p className="mt-3 rounded-md border-l-[3px] border-[var(--color-success)] bg-[var(--color-success-bg)] px-3 py-2 text-sm text-[var(--color-success-text)]">
        {expect}
      </p>
    </li>
  )
}

function OnboardingHowtoPage() {
  const navigate = useNavigate()
  const prerequisites = [
    m.platform_onboarding_prereq_admin(),
    m.platform_onboarding_prereq_owner_email(),
    m.platform_onboarding_prereq_unique_email(),
  ]
  const steps: StepItem[] = [
    {
      title: m.platform_onboarding_step_create_title(),
      body: m.platform_onboarding_step_create_body(),
      expect: m.platform_onboarding_step_create_expect(),
    },
    {
      title: m.platform_onboarding_step_owner_title(),
      body: m.platform_onboarding_step_owner_body(),
      expect: m.platform_onboarding_step_owner_expect(),
    },
    {
      title: m.platform_onboarding_step_chat_title(),
      body: m.platform_onboarding_step_chat_body(),
      expect: m.platform_onboarding_step_chat_expect(),
    },
    {
      title: m.platform_onboarding_step_knowledge_title(),
      body: m.platform_onboarding_step_knowledge_body(),
      expect: m.platform_onboarding_step_knowledge_expect(),
    },
    {
      title: m.platform_onboarding_step_instructions_title(),
      body: m.platform_onboarding_step_instructions_body(),
      expect: m.platform_onboarding_step_instructions_expect(),
    },
    {
      title: m.platform_onboarding_step_invite_title(),
      body: m.platform_onboarding_step_invite_body(),
      expect: m.platform_onboarding_step_invite_expect(),
    },
  ]

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10">
      <div className="mb-2 flex items-center justify-between">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.platform_onboarding_title()}
        </h1>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => void navigate({ to: '/admin/platform' })}
        >
          <ArrowLeft className="h-4 w-4" />
          {m.platform_back_to_platform()}
        </Button>
      </div>
      <p className="mb-6 text-sm text-gray-400">
        {m.platform_onboarding_description()}
      </p>

      <section className="space-y-2">
        <h2 className="text-[19px] font-display-bold text-gray-900">
          {m.platform_onboarding_before_you_start()}
        </h2>
        <ul className="list-disc space-y-1 pl-5 text-sm text-gray-700">
          {prerequisites.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      </section>

      <ol className="mt-6 list-none space-y-3.5 p-0">
        {steps.map((step, index) => (
          <Step key={step.title} n={index + 1} {...step} />
        ))}
      </ol>

      <div className="mt-6 flex justify-end">
        <Button
          type="button"
          onClick={() => void navigate({ to: '/admin/platform/new' })}
        >
          {m.platform_create_tenant()}
        </Button>
      </div>
    </div>
  )
}
