import { useState, useMemo } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Loader2, AlertTriangle, KeyRound } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { StepIndicator } from '@/components/ui/step-indicator'
import { apiFetch } from '@/lib/apiFetch'
import {
  useDeleteUserWithDispositions,
  useOffboardUser,
  type KbDisposition,
} from '@/hooks/useUserLifecycle'
import * as m from '@/paraglide/messages'

/**
 * SPEC-PORTAL-KB-OWNERSHIP-001 Phase 4 - admin offboarding wizard.
 *
 * Replaces the simple confirm-dialog with a per-KB disposition picker:
 *   - Org KBs the user solely owns: transfer to another active org
 *     member (default = the current admin, mirroring Google Workspace's
 *     "direct manager" pattern), or delete.
 *   - Personal KBs: always delete (no transfer option - REQ-2.4 / D2).
 *
 * Token revoke counts (REQ-2.1b / REQ-2.7) surface as an info banner
 * so the admin sees what auto-cleanup will happen alongside the
 * user-status flip.
 */

interface OffboardPreviewKb {
  kb_id: number
  slug: string
  name: string
  owner_type: 'org' | 'user'
  role_count: number
}

interface OffboardPreview {
  org_kbs_solely_owned: OffboardPreviewKb[]
  personal_kbs: OffboardPreviewKb[]
  api_keys_count: number
  mcp_tokens_count: number
}

interface DeletePreview {
  org_kbs_created: OffboardPreviewKb[]
  personal_kbs: OffboardPreviewKb[]
  api_keys_count: number
  mcp_tokens_count: number
}

interface AdminUser {
  zitadel_user_id: string
  email: string
  first_name: string
  last_name: string
  status: string
  invite_pending: boolean
}

type OrgDispositionAction = 'transfer' | 'delete'

interface OrgDispositionState {
  action: OrgDispositionAction
  transferTo: string
}

type WizardStep = 'impact' | 'transfer' | 'confirm'

interface OffboardWizardProps {
  userId: string
  /** Display label for the user being offboarded - shown in the dialog title. */
  userLabel: string
  /** Current admin's user-id; used as the default transfer-recipient. */
  currentAdminId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  mode?: 'offboard' | 'delete'
}

export function OffboardWizard({
  userId,
  userLabel,
  currentAdminId,
  open,
  onOpenChange,
  mode = 'offboard',
}: OffboardWizardProps) {
  const navigate = useNavigate()
  const offboardMutation = useOffboardUser()
  const deleteMutation = useDeleteUserWithDispositions()
  const lifecycleMutation = mode === 'delete' ? deleteMutation : offboardMutation
  const previewPath =
    mode === 'delete'
      ? `/api/admin/users/${userId}/delete-preview`
      : `/api/admin/users/${userId}/offboard-preview`

  // Preview is fetched once when the wizard opens. The empty-array
  // default keeps the rest of the component pure: no nullable list,
  // no "loading shimmer" branching for the disposition rows.
  const previewQuery = useQuery<OffboardPreview | DeletePreview>({
    queryKey: [mode === 'delete' ? 'delete-preview' : 'offboard-preview', userId],
    queryFn: async () =>
      apiFetch<OffboardPreview | DeletePreview>(
        previewPath,
      ),
    enabled: open,
    staleTime: 0,
  })

  // The transfer-receiver dropdown lists all active org members EXCEPT
  // the user being offboarded. Source is the same /api/admin/users
  // endpoint the user-list page already caches.
  const usersQuery = useQuery<{ users: AdminUser[] }>({
    queryKey: ['admin-users'],
    queryFn: async () => apiFetch<{ users: AdminUser[] }>(`/api/admin/users`),
    enabled: open,
  })

  const eligibleReceivers = useMemo(() => {
    if (!usersQuery.data) return []
    return usersQuery.data.users.filter(
      (u) =>
        u.zitadel_user_id !== userId &&
        u.status === 'active' &&
        !u.invite_pending,
    )
  }, [usersQuery.data, userId])

  // Per-org-KB disposition state. Default action is 'transfer' to the
  // current admin - picks up Mark's "default to admin" decision (D1).
  // Personal KBs have no state because their action is locked to delete.
  const [orgDispositions, setOrgDispositions] = useState<
    Record<number, OrgDispositionState>
  >({})
  const [step, setStep] = useState(0)

  const orgKbs =
    previewQuery.data
      ? 'org_kbs_created' in previewQuery.data
        ? previewQuery.data.org_kbs_created
        : previewQuery.data.org_kbs_solely_owned
      : []
  const personalKbs = previewQuery.data?.personal_kbs ?? []

  // Initialise / re-initialise dispositions when the preview lands.
  // useMemo would race the render; useState init is a one-shot. We
  // use a derived helper that reads from state and falls back to the
  // default for unseen kb_ids - keeps the picker stateless from the
  // user's POV until they touch it.
  function dispositionFor(kb: OffboardPreviewKb): OrgDispositionState {
    return (
      orgDispositions[kb.kb_id] ?? {
        action: 'transfer',
        transferTo: currentAdminId,
      }
    )
  }

  function setOrgKbAction(kb_id: number, action: OrgDispositionAction) {
    setOrgDispositions((prev) => ({
      ...prev,
      [kb_id]: {
        action,
        transferTo: prev[kb_id]?.transferTo ?? currentAdminId,
      },
    }))
  }

  function setOrgKbTransferTo(kb_id: number, transferTo: string) {
    setOrgDispositions((prev) => ({
      ...prev,
      [kb_id]: {
        action: prev[kb_id]?.action ?? 'transfer',
        transferTo,
      },
    }))
  }

  function buildDispositions(): KbDisposition[] {
    const orgRows: KbDisposition[] = orgKbs.map((kb) => {
      const d = dispositionFor(kb)
      if (d.action === 'transfer') {
        return { kb_id: kb.kb_id, action: 'transfer', transfer_to: d.transferTo }
      }
      return { kb_id: kb.kb_id, action: 'delete' }
    })
    const personalRows: KbDisposition[] = personalKbs.map((kb) => ({
      kb_id: kb.kb_id,
      action: 'delete',
    }))
    return [...orgRows, ...personalRows]
  }

  const isReady = !previewQuery.isLoading && !usersQuery.isLoading
  const isSubmitting = lifecycleMutation.isPending
  const previewError = previewQuery.error?.message
  const apiKeysCount = previewQuery.data?.api_keys_count ?? 0
  const mcpTokensCount = previewQuery.data?.mcp_tokens_count ?? 0
  const tokensCount = apiKeysCount + mcpTokensCount
  const transferCount = orgKbs.filter(
    (kb) => dispositionFor(kb).action === 'transfer',
  ).length
  const orgDeleteCount = orgKbs.length - transferCount
  const hasIncompleteTransfer = orgKbs.some((kb) => {
    const disposition = dispositionFor(kb)
    return disposition.action === 'transfer' && !disposition.transferTo
  })
  const stepIds: WizardStep[] =
    orgKbs.length > 0 ? ['impact', 'transfer', 'confirm'] : ['impact', 'confirm']
  const currentStepIndex = Math.min(step, stepIds.length - 1)
  const currentStep = stepIds[currentStepIndex]
  const isFinalStep = currentStep === 'confirm'
  const canGoNext =
    isReady &&
    !previewError &&
    (currentStep !== 'transfer' || !hasIncompleteTransfer)
  const wizardSteps = stepIds.map((stepId, index) => ({
    label:
      stepId === 'impact'
        ? m.admin_users_wizard_step_impact()
        : stepId === 'transfer'
          ? m.admin_users_wizard_step_transfer()
          : m.admin_users_wizard_step_confirm(),
    onClick: () => setStep(index),
  }))
  const impactRows = [
    ...(orgKbs.length > 0
      ? [{ label: m.admin_users_wizard_team_kbs_label(), value: orgKbs.length }]
      : []),
    ...(personalKbs.length > 0
      ? [
          {
            label: m.admin_users_wizard_personal_kbs_label(),
            value: personalKbs.length,
          },
        ]
      : []),
    ...(tokensCount > 0
      ? [{ label: m.admin_users_wizard_tokens_label(), value: tokensCount }]
      : []),
  ]
  const confirmRows = [
    ...(transferCount > 0
      ? [{ label: m.admin_users_wizard_summary_transfer(), value: transferCount }]
      : []),
    ...(orgDeleteCount > 0
      ? [
          {
            label: m.admin_users_wizard_summary_team_delete(),
            value: orgDeleteCount,
          },
        ]
      : []),
    ...(personalKbs.length > 0
      ? [
          {
            label: m.admin_users_wizard_summary_personal_delete(),
            value: personalKbs.length,
          },
        ]
      : []),
    ...(tokensCount > 0
      ? [{ label: m.admin_users_wizard_tokens_label(), value: tokensCount }]
      : []),
  ]
  const wizardDescription =
    !previewQuery.data || orgKbs.length > 0
      ? mode === 'delete'
        ? m.admin_users_delete_wizard_description()
        : m.admin_users_offboard_wizard_description()
      : personalKbs.length > 0
        ? mode === 'delete'
          ? m.admin_users_delete_wizard_personal_only_description()
          : m.admin_users_offboard_wizard_personal_only_description()
        : mode === 'delete'
          ? m.admin_users_delete_wizard_no_owned_data_description()
          : m.admin_users_offboard_wizard_no_owned_data_description()

  function handleOpenChange(next: boolean) {
    if (isSubmitting) return
    if (!next) {
      setOrgDispositions({})
      setStep(0)
    }
    onOpenChange(next)
  }

  function handleSubmit() {
    lifecycleMutation.mutate(
      { userId, kb_dispositions: buildDispositions() },
      {
        onSuccess: () => {
          setOrgDispositions({})
          setStep(0)
          onOpenChange(false)
          void navigate({ to: '/admin/users' })
        },
      },
    )
  }

  function handlePrimaryAction() {
    if (!isFinalStep) {
      setStep((current) => Math.min(current + 1, stepIds.length - 1))
      return
    }
    handleSubmit()
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-[var(--color-destructive-text)]">
            <AlertTriangle className="h-5 w-5" />
            {mode === 'delete'
              ? m.admin_users_delete_wizard_title({ name: userLabel })
              : m.admin_users_offboard_wizard_title({ name: userLabel })}
          </DialogTitle>
          <DialogDescription>
            {wizardDescription}
          </DialogDescription>
        </DialogHeader>

        {!isReady && (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            {m.admin_users_wizard_loading()}
          </div>
        )}

        {previewError && (
          <div className="border-y border-[var(--color-destructive)]/30 py-3 text-sm text-[var(--color-destructive-text)]">
            {m.admin_users_wizard_preview_error({ error: previewError })}
          </div>
        )}

        {isReady && previewQuery.data && (
          <div className="space-y-5">
            <StepIndicator steps={wizardSteps} currentIndex={currentStepIndex} />

            <div className="max-h-[56vh] overflow-y-auto pr-1 text-sm">
              {currentStep === 'impact' && (
                <div className="space-y-5">
                  <section className="space-y-2">
                    <h3 className="font-medium text-gray-900">
                      {m.admin_users_wizard_impact_title()}
                    </h3>
                    {impactRows.length > 0 ? (
                      <div className="divide-y divide-gray-200 border-y border-gray-200">
                        {impactRows.map((row) => (
                          <div
                            key={row.label}
                            className="flex items-center justify-between gap-4 py-3"
                          >
                            <span className="text-gray-600">{row.label}</span>
                            <span className="font-medium text-gray-900">
                              {row.value}
                            </span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="border-y border-gray-200 py-3 text-gray-600">
                        {m.admin_users_wizard_no_related_data()}
                      </p>
                    )}
                  </section>

                  {tokensCount > 0 && (
                    <div
                      className="flex items-start gap-2 border-y border-gray-200 py-3"
                      data-test-id="offboard-token-banner"
                    >
                      <KeyRound className="mt-0.5 h-4 w-4 text-gray-400" />
                      <div className="space-y-1">
                        <p className="font-medium text-gray-900">
                          {m.admin_users_wizard_tokens_title()}
                        </p>
                        <p className="text-gray-600">
                          {m.admin_users_wizard_tokens_description({
                            apiKeys: apiKeysCount,
                            mcpTokens: mcpTokensCount,
                          })}
                        </p>
                      </div>
                    </div>
                  )}

                </div>
              )}

              {currentStep === 'transfer' && (
                <section className="space-y-3">
                  <h3 className="font-medium text-gray-900">
                    {m.admin_users_wizard_transfer_kbs({ count: orgKbs.length })}
                  </h3>
                  <ul className="divide-y divide-gray-200 border-y border-gray-200">
                    {orgKbs.map((kb) => {
                      const d = dispositionFor(kb)
                      const actionId = `kb-action-${kb.kb_id}`
                      const transferId = `kb-transfer-${kb.kb_id}`

                      return (
                        <li
                          key={kb.kb_id}
                          className="py-4"
                          data-test-id={`org-kb-row-${kb.slug}`}
                        >
                          <div className="min-w-0">
                            <p className="truncate font-medium text-gray-900">
                              {kb.name}
                            </p>
                            <p className="truncate text-xs text-gray-400">
                              {kb.slug}
                            </p>
                          </div>
                          <div className="mt-3 grid gap-3 sm:grid-cols-[180px_minmax(0,1fr)]">
                            <div className="space-y-1.5">
                              <Label
                                htmlFor={actionId}
                                className="text-xs font-medium text-gray-600"
                              >
                                {m.admin_users_wizard_action_label()}
                              </Label>
                              <Select
                                id={actionId}
                                value={d.action}
                                onChange={(e) =>
                                  setOrgKbAction(
                                    kb.kb_id,
                                    e.target.value as OrgDispositionAction,
                                  )
                                }
                                containerClassName="w-full"
                              >
                                <option value="transfer">
                                  {m.admin_users_wizard_transfer()}
                                </option>
                                <option value="delete">
                                  {m.admin_users_delete()}
                                </option>
                              </Select>
                            </div>
                            {d.action === 'transfer' ? (
                              <div className="space-y-1.5">
                                <Label
                                  htmlFor={transferId}
                                  className="text-xs font-medium text-gray-600"
                                >
                                  {m.admin_users_wizard_recipient_label()}
                                </Label>
                                <Select
                                  id={transferId}
                                  value={d.transferTo}
                                  onChange={(e) =>
                                    setOrgKbTransferTo(kb.kb_id, e.target.value)
                                  }
                                  containerClassName="w-full"
                                >
                                  {eligibleReceivers.map((u) => (
                                    <option
                                      key={u.zitadel_user_id}
                                      value={u.zitadel_user_id}
                                    >
                                      {u.first_name} {u.last_name} ({u.email})
                                    </option>
                                  ))}
                                </Select>
                              </div>
                            ) : (
                              <p className="self-end pb-2 text-sm text-gray-500">
                                {m.admin_users_wizard_no_transfer()}
                              </p>
                            )}
                          </div>
                        </li>
                      )
                    })}
                  </ul>
                </section>
              )}

              {currentStep === 'confirm' && (
                <section className="space-y-3">
                  <h3 className="font-medium text-gray-900">
                    {m.admin_users_wizard_confirm_title()}
                  </h3>
                  {confirmRows.length > 0 ? (
                    <div className="divide-y divide-gray-200 border-y border-gray-200">
                      {confirmRows.map((row) => (
                        <div
                          key={row.label}
                          className="flex items-center justify-between gap-4 py-3"
                        >
                          <span className="text-gray-600">{row.label}</span>
                          <span className="font-medium text-gray-900">
                            {row.value}
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="border-y border-gray-200 py-3 text-gray-600">
                      {m.admin_users_wizard_no_related_data()}
                    </p>
                  )}
                  <p className="text-sm text-[var(--color-destructive-text)]">
                    {mode === 'delete'
                      ? m.admin_users_delete_wizard_final_warning()
                      : m.admin_users_offboard_wizard_final_warning()}
                  </p>
                </section>
              )}
            </div>
          </div>
        )}

        <DialogFooter>
          {currentStepIndex > 0 && (
            <Button
              type="button"
              variant="outline"
              onClick={() =>
                setStep((current) =>
                  Math.max(Math.min(current, stepIds.length - 1) - 1, 0),
                )
              }
              disabled={isSubmitting}
            >
              {m.admin_users_wizard_back()}
            </Button>
          )}
          <Button
            type="button"
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isSubmitting}
          >
            {m.admin_users_cancel()}
          </Button>
          <Button
            variant={isFinalStep ? 'destructive' : 'default'}
            onClick={handlePrimaryAction}
            disabled={!canGoNext || isSubmitting}
            data-test-id="offboard-submit-button"
          >
            {isSubmitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            {isFinalStep
              ? mode === 'delete'
                ? m.admin_users_action_delete()
                : m.admin_users_action_offboard()
              : m.admin_users_wizard_next()}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
