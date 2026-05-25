import { useState, useMemo } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Loader2, AlertTriangle, KeyRound } from 'lucide-react'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Select } from '@/components/ui/select'
import { apiFetch } from '@/lib/apiFetch'
import { useOffboardUser, type KbDisposition } from '@/hooks/useUserLifecycle'

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

interface OffboardWizardProps {
  userId: string
  /** Display label for the user being offboarded - shown in the dialog title. */
  userLabel: string
  /** Current admin's user-id; used as the default transfer-recipient. */
  currentAdminId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function OffboardWizard({
  userId,
  userLabel,
  currentAdminId,
  open,
  onOpenChange,
}: OffboardWizardProps) {
  const navigate = useNavigate()
  const offboardMutation = useOffboardUser()

  // Preview is fetched once when the wizard opens. The empty-array
  // default keeps the rest of the component pure: no nullable list,
  // no "loading shimmer" branching for the disposition rows.
  const previewQuery = useQuery<OffboardPreview>({
    queryKey: ['offboard-preview', userId],
    queryFn: async () =>
      apiFetch<OffboardPreview>(
        `/api/admin/users/${userId}/offboard-preview`,
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

  const orgKbs = previewQuery.data?.org_kbs_solely_owned ?? []
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
  const isSubmitting = offboardMutation.isPending
  const previewError = previewQuery.error?.message

  function handleOpenChange(next: boolean) {
    if (isSubmitting) return
    if (!next) {
      setOrgDispositions({})
    }
    onOpenChange(next)
  }

  function handleSubmit() {
    offboardMutation.mutate(
      { userId, kb_dispositions: buildDispositions() },
      {
        onSuccess: () => {
          onOpenChange(false)
          void navigate({ to: '/admin/users' })
        },
      },
    )
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent className="max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5" />
            {userLabel} offboarden
          </AlertDialogTitle>
          <AlertDialogDescription>
            Persoonlijke kennisbanken worden definitief verwijderd. Voor team-
            kennisbanken kies je per stuk: overdragen aan een collega of
            permanent verwijderen. Twijfel je? Schors de gebruiker tijdelijk
            in plaats van offboarden - dan blijft alle data behouden.
          </AlertDialogDescription>
        </AlertDialogHeader>

        {!isReady && (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            Voorbereiden...
          </div>
        )}

        {previewError && (
          <div className="rounded-md border border-destructive bg-destructive/5 p-3 text-sm text-destructive">
            Kon offboard-preview niet laden: {previewError}
          </div>
        )}

        {isReady && previewQuery.data && (
          <div className="space-y-4 text-sm max-h-[60vh] overflow-y-auto">
            {/* Token-revoke summary (REQ-2.1b / REQ-2.7) */}
            {(previewQuery.data.api_keys_count > 0 ||
              previewQuery.data.mcp_tokens_count > 0) && (
              <div
                className="flex items-start gap-2 rounded-md border border-border bg-secondary p-3"
                data-test-id="offboard-token-banner"
              >
                <KeyRound className="h-4 w-4 mt-0.5 text-muted-foreground" />
                <div>
                  <p className="font-medium">
                    Toegangstokens worden automatisch ingetrokken
                  </p>
                  <p className="text-muted-foreground">
                    {previewQuery.data.api_keys_count} API-key
                    {previewQuery.data.api_keys_count !== 1 ? 's' : ''} en{' '}
                    {previewQuery.data.mcp_tokens_count} MCP-token
                    {previewQuery.data.mcp_tokens_count !== 1 ? 's' : ''} van
                    deze gebruiker worden tegelijk met de offboard verwijderd.
                  </p>
                </div>
              </div>
            )}

            {/* Org-KBs the user solely owns */}
            {orgKbs.length > 0 && (
              <section>
                <h3 className="font-medium mb-2">
                  Team-kennisbanken ({orgKbs.length})
                </h3>
                <ul className="space-y-2">
                  {orgKbs.map((kb) => {
                    const d = dispositionFor(kb)
                    return (
                      <li
                        key={kb.kb_id}
                        className="rounded-md border border-border p-3"
                        data-test-id={`org-kb-row-${kb.slug}`}
                      >
                        <div className="flex items-center justify-between gap-2 flex-wrap">
                          <div>
                            <p className="font-medium">{kb.name}</p>
                            <p className="text-xs text-muted-foreground">
                              {kb.slug}
                            </p>
                          </div>
                          <Select
                            value={d.action}
                            onChange={(e) =>
                              setOrgKbAction(kb.kb_id, e.target.value as OrgDispositionAction)
                            }
                            className="w-44"
                          >
                            <option value="transfer">Overdragen</option>
                            <option value="delete">Verwijderen</option>
                          </Select>
                        </div>
                        {d.action === 'transfer' && (
                          <div className="mt-2 flex items-center gap-2">
                            <span className="text-xs text-muted-foreground">aan</span>
                            <Select
                              value={d.transferTo}
                              onChange={(e) => setOrgKbTransferTo(kb.kb_id, e.target.value)}
                              className="w-full max-w-sm"
                            >
                              {eligibleReceivers.map((u) => (
                                <option key={u.zitadel_user_id} value={u.zitadel_user_id}>
                                  {u.first_name} {u.last_name} ({u.email})
                                </option>
                              ))}
                            </Select>
                          </div>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </section>
            )}

            {/* Personal KBs - locked to delete */}
            {personalKbs.length > 0 && (
              <section>
                <h3 className="font-medium mb-2">
                  Persoonlijke kennisbanken ({personalKbs.length})
                </h3>
                <ul className="space-y-2">
                  {personalKbs.map((kb) => (
                    <li
                      key={kb.kb_id}
                      className="rounded-md border border-border p-3"
                      data-test-id={`personal-kb-row-${kb.slug}`}
                    >
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <div>
                          <p className="font-medium">{kb.name}</p>
                          <p className="text-xs text-muted-foreground">{kb.slug}</p>
                        </div>
                        <span
                          className="text-xs rounded-md bg-destructive/10 text-destructive px-2 py-1"
                          title="Persoonlijke kennisbanken worden bij offboarding altijd verwijderd"
                        >
                          Wordt verwijderd
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {orgKbs.length === 0 && personalKbs.length === 0 && (
              <p className="text-muted-foreground">
                Deze gebruiker heeft geen eigen kennisbanken. Offboarden gaat
                door zonder data-overdracht.
              </p>
            )}
          </div>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isSubmitting}>Annuleren</AlertDialogCancel>
          <Button
            variant="destructive"
            onClick={handleSubmit}
            disabled={!isReady || isSubmitting || !!previewError}
            data-test-id="offboard-submit-button"
          >
            {isSubmitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            Offboard
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
