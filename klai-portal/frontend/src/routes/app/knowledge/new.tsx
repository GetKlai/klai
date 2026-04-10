import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Globe,
  Users,
  Lock,
  ArrowLeft,
  ArrowRight,
  Check,
  User,
  Brain,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent } from '@/components/ui/card'
import * as m from '@/paraglide/messages'
import { apiFetch, ApiError } from '@/lib/apiFetch'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { MemberPicker } from './new._components/MemberPicker'
import type { WizardData, Step, OrgGroup } from './new._types'

export const Route = createFileRoute('/app/knowledge/new')({
  component: () => (
    <ProductGuard product="knowledge">
      <NewKnowledgeBasePage />
    </ProductGuard>
  ),
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
}

// ---------------------------------------------------------------------------
// Main page component
// ---------------------------------------------------------------------------

function NewKnowledgeBasePage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const [step, setStep] = useState<Step>(1)
  const [errorKey, setErrorKey] = useState<'conflict' | 'generic' | null>(null)

  const [data, setData] = useState<WizardData>({
    name: '',
    slug: '',
    slugManuallyEdited: false,
    description: '',
    ownerType: 'org',
    visibilityMode: 'org',
    allowContribute: true,
    initialGroups: [],
    initialUsers: [],
  })

  // Queries for member picker (step 3)
  const { data: groupsData } = useQuery({
    queryKey: ['app-groups'],
    queryFn: () =>
      apiFetch<{ groups: OrgGroup[] }>(
        '/api/app/groups',
        token
      ),
    enabled: !!token && data.ownerType === 'org' && step >= 3,
  })

  const { data: usersData } = useQuery({
    queryKey: ['app-users'],
    queryFn: () =>
      apiFetch<{
        users: { zitadel_user_id: string; display_name: string; email: string }[]
      }>('/api/app/users', token),
    enabled: !!token && data.ownerType === 'org' && step >= 3,
  })

  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      const visibility =
        data.visibilityMode === 'public'
          ? 'public'
          : data.visibilityMode === 'org'
            ? 'internal'
            : 'private'
      const defaultOrgRole =
        data.visibilityMode === 'restricted'
          ? null
          : data.allowContribute
            ? 'contributor'
            : 'viewer'

      return apiFetch<{ slug: string }>(`/api/app/knowledge-bases`, token, {
        method: 'POST',
        body: JSON.stringify({
          name: data.name,
          slug: data.slug,
          description: data.description || undefined,
          visibility,
          owner_type: data.ownerType,
          default_org_role: defaultOrgRole,
          initial_members:
            data.ownerType === 'org'
              ? [
                  ...data.initialGroups.map((g) => ({
                    type: 'group',
                    id: String(g.id),
                    role: g.role,
                  })),
                  ...data.initialUsers.map((u) => ({
                    type: 'user',
                    id: u.id,
                    role: u.role,
                  })),
                ]
              : undefined,
        }),
      })
    },
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge/$kbSlug', params: { kbSlug: result.slug } })
    },
    onError: (err: Error) => {
      setErrorKey(err instanceof ApiError && err.status === 409 ? 'conflict' : 'generic')
    },
  })

  // Navigation
  const isPersonal = data.ownerType === 'user'

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

  function handleStepClick(target: Step) {
    if (target >= step) return
    if (isPersonal && (target === 2 || target === 3)) return
    setStep(target)
  }

  // Validation
  const isRestrictedEmpty =
    data.visibilityMode === 'restricted' &&
    data.initialGroups.length === 0 &&
    data.initialUsers.length === 0

  const step1Valid = data.name.trim() !== '' && data.slug.trim() !== ''
  const step3Valid = data.visibilityMode !== 'restricted' || !isRestrictedEmpty

  function canAdvance(): boolean {
    if (step === 1) return step1Valid
    if (step === 2) return true
    if (step === 3) return step3Valid
    return true
  }

  return (
    <div className="p-6 max-w-lg">
      {/* Header with back/cancel */}
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-xl/none font-semibold text-[var(--color-foreground)]">
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

      {/* Step indicator */}
      <StepIndicator
        currentStep={step}
        isPersonal={isPersonal}
        onStepClick={handleStepClick}
      />

      {/* Step content */}
      <div className="mt-6">
        {step === 1 && <StepName data={data} setData={setData} errorKey={errorKey} />}
        {step === 2 && <StepAccess data={data} setData={setData} />}
        {step === 3 && (
          <StepPermissions
            data={data}
            setData={setData}
            groups={groupsData?.groups ?? []}
            users={usersData?.users ?? []}
          />
        )}
        {step === 4 && (
          <StepConfirm
            data={data}
            isPending={isPending}
            errorKey={errorKey}
            onSubmit={() => {
              setErrorKey(null)
              mutate()
            }}
            onEditSlug={() => setStep(1)}
          />
        )}
      </div>

      {/* Navigation buttons (steps 1-3) */}
      {step < 4 && (
        <div className="flex justify-end pt-6">
          <Button onClick={handleNext} disabled={!canAdvance()}>
            {m.knowledge_wizard_next()}
            <ArrowRight className="h-4 w-4 ml-2" />
          </Button>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step indicator
// ---------------------------------------------------------------------------

function StepIndicator({
  currentStep,
  isPersonal,
  onStepClick,
}: {
  currentStep: Step
  isPersonal: boolean
  onStepClick: (step: Step) => void
}) {
  const steps: { step: Step; label: string; skipped: boolean }[] = [
    { step: 1, label: m.knowledge_wizard_step_name(), skipped: false },
    { step: 2, label: m.knowledge_wizard_step_access(), skipped: isPersonal },
    { step: 3, label: m.knowledge_wizard_step_permissions(), skipped: isPersonal },
    { step: 4, label: m.knowledge_wizard_step_confirm(), skipped: false },
  ]

  return (
    <div className="flex items-center gap-2">
      {steps.map(({ step, label, skipped }, i) => {
        if (skipped) return null

        const isActive = currentStep === step
        const isCompleted = currentStep > step || (isPersonal && step === 1 && currentStep === 4)
        const isClickable = isCompleted

        return (
          <div key={step} className="flex items-center gap-2">
            {i > 0 && !steps[i - 1].skipped && (
              <div
                className={[
                  'h-px w-6',
                  isCompleted || isActive
                    ? 'bg-[var(--color-accent)]'
                    : 'bg-[var(--color-border)]',
                ].join(' ')}
              />
            )}
            <button
              type="button"
              onClick={() => isClickable && onStepClick(step)}
              disabled={!isClickable}
              className={[
                'flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors',
                isActive
                  ? 'bg-[var(--color-accent)] text-white'
                  : isCompleted
                    ? 'bg-[var(--color-accent)]/10 text-[var(--color-accent)] cursor-pointer hover:bg-[var(--color-accent)]/20'
                    : 'bg-[var(--color-secondary)] text-[var(--color-muted-foreground)] cursor-default',
              ].join(' ')}
            >
              {isCompleted && !isActive ? (
                <Check className="h-3 w-3" />
              ) : (
                <span>{step}</span>
              )}
              {label}
            </button>
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 1: Name & Scope
// ---------------------------------------------------------------------------

function StepName({
  data,
  setData,
  errorKey,
}: {
  data: WizardData
  setData: React.Dispatch<React.SetStateAction<WizardData>>
  errorKey: 'conflict' | 'generic' | null
}) {
  function handleNameChange(value: string) {
    setData((prev) => ({
      ...prev,
      name: value,
      slug: prev.slugManuallyEdited ? prev.slug : slugify(value),
    }))
  }

  function handleSlugChange(value: string) {
    setData((prev) => ({
      ...prev,
      slug: slugify(value),
      slugManuallyEdited: true,
    }))
  }

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm font-medium text-[var(--color-foreground)]">
        {m.knowledge_wizard_title_step1()}
      </p>

      {/* Scope picker */}
      <div className="flex flex-col gap-1.5">
        <Label>{m.knowledge_new_scope_label()}</Label>
        <div className="grid grid-cols-2 gap-3">
          {(['org', 'user'] as const).map((type) => (
            <button
              key={type}
              type="button"
              onClick={() =>
                setData((prev) => ({
                  ...prev,
                  ownerType: type,
                  visibilityMode: type === 'user' ? 'org' : prev.visibilityMode,
                }))
              }
              className={[
                'flex flex-col items-start gap-1 rounded-xl border p-4 text-left transition-all',
                data.ownerType === type
                  ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5 ring-1 ring-[var(--color-accent)]'
                  : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50',
              ].join(' ')}
            >
              {type === 'org' ? (
                <Users className="h-4 w-4 text-[var(--color-accent)]" />
              ) : (
                <User className="h-4 w-4 text-[var(--color-accent)]" />
              )}
              <span className="text-sm font-medium text-[var(--color-foreground)]">
                {type === 'org' ? m.knowledge_new_scope_org() : m.knowledge_new_scope_personal()}
              </span>
              <span className="text-xs text-[var(--color-muted-foreground)]">
                {type === 'org'
                  ? m.knowledge_new_scope_org_description()
                  : m.knowledge_new_scope_personal_description()}
              </span>
            </button>
          ))}
        </div>
      </div>

      {/* Name */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="kb-name">{m.knowledge_new_name_label()}</Label>
        <Input
          id="kb-name"
          value={data.name}
          onChange={(e) => handleNameChange(e.target.value)}
          placeholder={m.knowledge_new_name_placeholder()}
        />
      </div>

      {/* Slug */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="kb-slug">{m.knowledge_new_slug_label()}</Label>
        <Input
          id="kb-slug"
          value={data.slug}
          onChange={(e) => handleSlugChange(e.target.value)}
          pattern="[a-z0-9\-]+"
        />
        <p className="text-xs text-[var(--color-muted-foreground)]">
          {m.knowledge_new_slug_hint()}
        </p>
        {errorKey === 'conflict' && (
          <p className="text-xs text-[var(--color-destructive)]">
            {m.knowledge_new_slug_conflict()}
          </p>
        )}
      </div>

      {/* Description */}
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="kb-description">{m.knowledge_wizard_description_label()}</Label>
        <textarea
          id="kb-description"
          value={data.description}
          onChange={(e) => setData((prev) => ({ ...prev, description: e.target.value }))}
          placeholder={m.knowledge_wizard_description_placeholder()}
          rows={3}
          className="w-full rounded-md border border-[var(--color-border)] bg-transparent px-3 py-2 text-sm text-[var(--color-foreground)] outline-none transition-colors placeholder:text-[var(--color-muted-foreground)] focus:ring-2 focus:ring-[var(--color-ring)] disabled:cursor-not-allowed disabled:opacity-50 resize-none"
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 2: Access / Visibility
// ---------------------------------------------------------------------------

function StepAccess({
  data,
  setData,
}: {
  data: WizardData
  setData: React.Dispatch<React.SetStateAction<WizardData>>
}) {
  const options = [
    {
      key: 'public' as const,
      icon: Globe,
      title: m.knowledge_sharing_visibility_public(),
      desc: m.knowledge_sharing_visibility_public_description(),
    },
    {
      key: 'org' as const,
      icon: Users,
      title: m.knowledge_sharing_visibility_org(),
      desc: m.knowledge_sharing_visibility_org_description(),
    },
    {
      key: 'restricted' as const,
      icon: Lock,
      title: m.knowledge_sharing_visibility_restricted(),
      desc: m.knowledge_sharing_visibility_restricted_description(),
    },
  ] as const

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm font-medium text-[var(--color-foreground)]">
        {m.knowledge_wizard_title_step2({ name: data.name })}
      </p>

      <div className="flex flex-col gap-2">
        {options.map(({ key, icon: Icon, title, desc }) => (
          <button
            key={key}
            type="button"
            onClick={() =>
              setData((prev) => ({
                ...prev,
                visibilityMode: key,
                allowContribute: key === 'restricted' ? false : prev.allowContribute,
              }))
            }
            className={[
              'flex items-start gap-3 rounded-xl border p-4 text-left transition-all',
              data.visibilityMode === key
                ? 'border-[var(--color-accent)] bg-[var(--color-accent)]/5 ring-1 ring-[var(--color-accent)]'
                : 'border-[var(--color-border)] bg-[var(--color-card)] hover:border-[var(--color-accent)]/50',
            ].join(' ')}
          >
            <Icon className="h-5 w-5 mt-0.5 text-[var(--color-accent)]" />
            <div>
              <span className="text-sm font-medium text-[var(--color-foreground)]">
                {title}
              </span>
              <span className="block text-xs text-[var(--color-muted-foreground)]">{desc}</span>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 3: Permissions
// ---------------------------------------------------------------------------

function StepPermissions({
  data,
  setData,
  groups,
  users,
}: {
  data: WizardData
  setData: React.Dispatch<React.SetStateAction<WizardData>>
  groups: OrgGroup[]
  users: { zitadel_user_id: string; display_name: string; email: string }[]
}) {
  const isRestricted = data.visibilityMode === 'restricted'
  const minRole = !isRestricted && data.allowContribute ? 'contributor' : 'viewer'

  const isRestrictedEmpty =
    isRestricted && data.initialGroups.length === 0 && data.initialUsers.length === 0

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm font-medium text-[var(--color-foreground)]">
        {isRestricted
          ? m.knowledge_wizard_title_step3_restricted({ name: data.name })
          : m.knowledge_wizard_title_step3({ name: data.name })}
      </p>

      {/* Variant A: org/public — default role section */}
      {!isRestricted && (
        <Card>
          <CardContent className="pt-4">
            <div className="flex flex-col gap-3">
              <p className="text-sm text-[var(--color-foreground)]">
                {m.knowledge_wizard_default_role_label()}
              </p>
              <label className="flex items-start gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={data.allowContribute}
                  onChange={(e) =>
                    setData((prev) => ({ ...prev, allowContribute: e.target.checked }))
                  }
                  className="mt-1 h-4 w-4 rounded border-[var(--color-border)] text-[var(--color-accent)] focus:ring-[var(--color-ring)]"
                />
                <div>
                  <span className="text-sm font-medium text-[var(--color-foreground)]">
                    {m.knowledge_wizard_contributor_checkbox()}
                  </span>
                  <span className="block text-xs text-[var(--color-muted-foreground)]">
                    {m.knowledge_sharing_contributor_toggle_description()}
                  </span>
                </div>
              </label>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Variant B: restricted — explanation */}
      {isRestricted && (
        <p className="text-sm text-[var(--color-muted-foreground)]">
          {m.knowledge_wizard_restricted_desc()}
        </p>
      )}

      {/* Extra permissions heading for org/public */}
      {!isRestricted && (
        <div>
          <p className="text-sm font-medium text-[var(--color-foreground)]">
            {m.knowledge_wizard_extra_permissions_title()}
          </p>
          <p className="text-xs text-[var(--color-muted-foreground)]">
            {m.knowledge_wizard_extra_permissions_desc()}
          </p>
        </div>
      )}

      {/* Member picker */}
      <MemberPicker
        initialGroups={data.initialGroups}
        setInitialGroups={(fn) =>
          setData((prev) => ({
            ...prev,
            initialGroups: typeof fn === 'function' ? fn(prev.initialGroups) : fn,
          }))
        }
        initialUsers={data.initialUsers}
        setInitialUsers={(fn) =>
          setData((prev) => ({
            ...prev,
            initialUsers: typeof fn === 'function' ? fn(prev.initialUsers) : fn,
          }))
        }
        availableGroups={groups}
        availableUsers={users}
        minRole={minRole}
        isRestrictedEmpty={isRestricted ? isRestrictedEmpty : false}
      />

      {/* Owner info */}
      <p className="text-xs text-[var(--color-muted-foreground)] italic">
        {m.knowledge_wizard_owner_info()}
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Step 4: Confirm
// ---------------------------------------------------------------------------

function StepConfirm({
  data,
  isPending,
  errorKey,
  onSubmit,
  onEditSlug,
}: {
  data: WizardData
  isPending: boolean
  errorKey: 'conflict' | 'generic' | null
  onSubmit: () => void
  onEditSlug: () => void
}) {
  const visibilityLabel =
    data.visibilityMode === 'public'
      ? m.knowledge_sharing_visibility_public()
      : data.visibilityMode === 'org'
        ? m.knowledge_sharing_visibility_org()
        : m.knowledge_sharing_visibility_restricted()

  const VisibilityIcon =
    data.visibilityMode === 'public' ? Globe : data.visibilityMode === 'org' ? Users : Lock

  return (
    <div className="flex flex-col gap-5">
      <p className="text-sm font-medium text-[var(--color-foreground)]">
        {m.knowledge_wizard_confirm_title()}
      </p>

      <Card>
        <CardContent className="pt-4">
          <div className="space-y-3 text-sm">
            {/* Name + slug */}
            <div className="flex items-start gap-2">
              <Brain className="h-4 w-4 mt-0.5 text-[var(--color-accent)]" />
              <div>
                <p className="font-medium text-[var(--color-foreground)]">{data.name}</p>
                <p className="text-xs text-[var(--color-muted-foreground)]">{data.slug}</p>
              </div>
            </div>

            {/* Description */}
            {data.description && (
              <p className="text-[var(--color-muted-foreground)] italic">
                &ldquo;{data.description}&rdquo;
              </p>
            )}

            {/* Visibility (org scope only) */}
            {data.ownerType === 'org' && (
              <div className="flex items-center gap-2">
                <VisibilityIcon className="h-4 w-4 text-[var(--color-accent)]" />
                <span className="text-[var(--color-foreground)]">{visibilityLabel}</span>
              </div>
            )}

            {/* Default org role */}
            {data.ownerType === 'org' && data.visibilityMode !== 'restricted' && (
              <p className="text-[var(--color-muted-foreground)]">
                {m.knowledge_sharing_summary_org_default({
                  role: data.allowContribute ? 'contributor' : 'viewer',
                })}
              </p>
            )}

            {/* Personal scope info */}
            {data.ownerType === 'user' && (
              <p className="text-[var(--color-muted-foreground)]">
                {m.knowledge_wizard_personal_only()}
              </p>
            )}

            {/* Extra members */}
            {data.ownerType === 'org' &&
              (data.initialGroups.length > 0 || data.initialUsers.length > 0) && (
                <div className="border-t border-[var(--color-border)] pt-2">
                  {data.visibilityMode === 'restricted' ? (
                    <p className="text-xs font-medium text-[var(--color-muted-foreground)] mb-1">
                      {m.knowledge_sharing_summary_only_shared()}
                    </p>
                  ) : (
                    <p className="text-xs font-medium text-[var(--color-muted-foreground)] mb-1">
                      {m.knowledge_wizard_extra_permissions_title()}:
                    </p>
                  )}
                  {data.initialGroups.map((g) => (
                    <p
                      key={g.id}
                      className="text-xs text-[var(--color-muted-foreground)] pl-3"
                    >
                      &bull; {g.name} ({g.role})
                    </p>
                  ))}
                  {data.initialUsers.map((u) => (
                    <p
                      key={u.id}
                      className="text-xs text-[var(--color-muted-foreground)] pl-3"
                    >
                      &bull; {u.name || u.email} ({u.role})
                    </p>
                  ))}
                </div>
              )}

            {/* Docs auto */}
            <p className="text-[var(--color-muted-foreground)]">
              {m.knowledge_sharing_summary_docs_auto()}
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Slug conflict error with edit link */}
      {errorKey === 'conflict' && (
        <div className="flex items-center gap-2 text-sm">
          <p className="text-[var(--color-destructive)]">
            {m.knowledge_new_slug_conflict()}
          </p>
          <Button type="button" variant="link" size="sm" onClick={onEditSlug} className="px-0">
            {m.knowledge_wizard_edit_slug()}
          </Button>
        </div>
      )}
      {errorKey === 'generic' && (
        <p className="text-sm text-[var(--color-destructive)]">{m.knowledge_new_error()}</p>
      )}

      {/* Submit button */}
      <div className="flex justify-end pt-2">
        <Button onClick={onSubmit} disabled={isPending}>
          {m.knowledge_wizard_create_button()}
        </Button>
      </div>
    </div>
  )
}
