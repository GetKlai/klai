import { useState } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ArrowLeft, ArrowRight } from 'lucide-react'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { Button } from '@/components/ui/button'
import { StepIndicator, type StepItem } from '@/components/ui/step-indicator'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { useKBQuota } from '@/hooks/useKBQuota'
import { useAuth } from '@/lib/auth'
import * as m from '@/paraglide/messages'
import { StepAccess } from './new._components/-StepAccess'
import { StepConfirm } from './new._components/-StepConfirm'
import { StepName } from './new._components/-StepName'
import { StepPermissions } from './new._components/-StepPermissions'
import {
  useCreateKnowledgeBaseMutation,
  useKnowledgeWizardMembers,
} from './new._wizard-hooks'
import type { Step, WizardData, WizardErrorKey } from './new._types'

export const Route = createFileRoute('/app/knowledge/new')({
  component: () => (
    <ProductGuard product="knowledge">
      <NewKnowledgeBasePage />
    </ProductGuard>
  ),
})

function NewKnowledgeBasePage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const { user } = useCurrentUser()
  const { canCreateKB } = useKBQuota()

  const isLimitedPlan = user ? !user.hasCapability('kb.connectors') : false
  const [step, setStep] = useState<Step>(1)
  const [errorKey, setErrorKey] = useState<WizardErrorKey>(null)

  const [data, setData] = useState<WizardData>({
    name: '',
    slug: '',
    slugManuallyEdited: false,
    description: '',
    ownerType: isLimitedPlan ? 'user' : 'org',
    visibilityMode: 'org',
    allowContribute: true,
    initialGroups: [],
    initialUsers: [],
  })

  const { groups, users } = useKnowledgeWizardMembers({
    isAuthenticated: auth.isAuthenticated,
    ownerType: data.ownerType,
    step,
  })

  const { mutate, isPending } = useCreateKnowledgeBaseMutation({
    data,
    onErrorKey: setErrorKey,
  })

  const isPersonal = data.ownerType === 'user'
  const step1Valid = data.name.trim() !== '' && data.slug.trim() !== ''
  const step3Valid =
    data.visibilityMode !== 'restricted' ||
    data.initialGroups.length > 0 ||
    data.initialUsers.length > 0

  function handleNext() {
    if (step === 1 && isPersonal) {
      setStep(4)
    } else if (step < 4) {
      setStep((step + 1) as Step)
    }
  }

  function handleBack() {
    if (step === 4 && isPersonal) {
      setStep(1)
    } else if (step > 1) {
      setStep((step - 1) as Step)
    }
  }

  function canAdvance(): boolean {
    if (step === 1) return step1Valid
    if (step === 2) return true
    if (step === 3) return step3Valid
    return true
  }

  return (
    <div className="mx-auto max-w-lg px-6 pt-6 pb-10">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.knowledge_new_heading()}
        </h1>
        {step === 1 ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void navigate({ to: '/app/knowledge' })}
          >
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.knowledge_wizard_cancel()}
          </Button>
        ) : (
          <Button type="button" variant="ghost" size="sm" onClick={handleBack}>
            <ArrowLeft className="h-4 w-4 mr-2" />
            {m.knowledge_wizard_back()}
          </Button>
        )}
      </div>

      <StepIndicator steps={getStepItems(isPersonal, setStep)} currentIndex={getStepIndex(isPersonal, step)} />

      <div className="mt-6">
        {step === 1 && (
          <StepName
            data={data}
            setData={setData}
            errorKey={errorKey}
            isLimitedPlan={isLimitedPlan}
          />
        )}
        {step === 2 && <StepAccess data={data} setData={setData} />}
        {step === 3 && (
          <StepPermissions
            data={data}
            setData={setData}
            groups={groups}
            users={users}
          />
        )}
        {step === 4 && (
          <StepConfirm
            data={data}
            isPending={isPending}
            errorKey={errorKey}
            canCreateKB={canCreateKB}
            onSubmit={() => {
              setErrorKey(null)
              mutate()
            }}
            onEditSlug={() => setStep(1)}
          />
        )}
      </div>

      {step < 4 && (
        <div className="flex pt-6">
          <Button onClick={handleNext} disabled={!canAdvance()}>
            {m.knowledge_wizard_next()}
            <ArrowRight className="h-4 w-4 ml-2" />
          </Button>
        </div>
      )}
    </div>
  )
}

function getStepItems(isPersonal: boolean, setStep: (step: Step) => void): StepItem[] {
  const allSteps: StepItem[] = [
    { label: m.knowledge_wizard_step_name(), onClick: () => setStep(1) },
    { label: m.knowledge_wizard_step_access(), onClick: () => setStep(2) },
    { label: m.knowledge_wizard_step_permissions(), onClick: () => setStep(3) },
    { label: m.knowledge_wizard_step_confirm() },
  ]

  return isPersonal ? [allSteps[0], allSteps[3]] : allSteps
}

function getStepIndex(isPersonal: boolean, step: Step): number {
  return isPersonal ? (step === 1 ? 0 : 1) : step - 1
}
