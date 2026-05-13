import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, AlertTriangle } from 'lucide-react'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogFooter,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { apiFetch } from '@/lib/apiFetch'

/**
 * SPEC-PORTAL-KB-OWNERSHIP-001 REQ-1.1 — header-based admin-override token.
 * Mirrors the I-CONFIRM-REMOVAL precedent in klai-infra/sync-env.yml: a
 * typed string forces explicit operator intent, impossible to set by an
 * accidental click-through.
 */
const ADMIN_OVERRIDE_HEADER = 'X-Admin-Override-Confirm'
const ADMIN_OVERRIDE_VALUE = 'I-WAS-NOT-CREATOR'

interface DeleteKbModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kbSlug: string
  kbName: string
  itemCount: number | null
  connectorCount: number
  hasGitea: boolean
  hasDocs: boolean
  /**
   * `'self'` — caller is owner / creator. Existing UX:
   *    type the kb-slug to confirm; no override header sent.
   * `'admin-override'` — caller is org admin but NOT the creator
   *    (SPEC-PORTAL-KB-OWNERSHIP-001 REQ-1.1). Yellow banner names the
   *    creator; confirm-gate becomes "type DELETE"; the
   *    `X-Admin-Override-Confirm: I-WAS-NOT-CREATOR` header is attached
   *    only after the typed confirmation.
   */
  mode?: 'self' | 'admin-override'
  /**
   * Display name (or fallback to user-id) of the original creator.
   * Used in the admin-override banner so the deleting admin sees whose
   * KB they are about to remove. Ignored when `mode === 'self'`.
   */
  creatorName?: string | null
}

export function DeleteKbModal({
  open,
  onOpenChange,
  kbSlug,
  kbName,
  itemCount,
  connectorCount,
  hasGitea,
  hasDocs,
  mode = 'self',
  creatorName,
}: DeleteKbModalProps) {
  const [confirmValue, setConfirmValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const isAdminOverride = mode === 'admin-override'
  // The confirm-gate text differs per mode. In self-mode the user types
  // the kb-slug (existing behaviour); in admin-override mode the user
  // types the literal string DELETE (matches the typed-token shape of
  // the override-header value, kept simpler than the slug for the
  // less-frequent admin path).
  const confirmTarget = isAdminOverride ? 'DELETE' : kbSlug

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const headers: Record<string, string> = {}
      // REQ-1.1 — only attach the override header in admin-override mode.
      // The owner pad must NEVER send it (defense-in-depth: the header
      // alone bypasses the owner role check on the backend, so leaking
      // it across modes would let a non-owner get a 204).
      if (isAdminOverride) {
        headers[ADMIN_OVERRIDE_HEADER] = ADMIN_OVERRIDE_VALUE
      }
      await apiFetch(`/api/app/knowledge-bases/${kbSlug}`, {
        method: 'DELETE',
        headers,
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge' })
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const isMatch = confirmValue === confirmTarget
  const isPending = deleteMutation.isPending

  function handleOpenChange(next: boolean) {
    if (isPending) return
    if (!next) {
      setConfirmValue('')
      setError(null)
    }
    onOpenChange(next)
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5" />
            Knowledge base permanent verwijderen
          </AlertDialogTitle>
        </AlertDialogHeader>

        <div className="space-y-3 text-sm">
          {isAdminOverride && (
            <div
              className="rounded-md border border-warning bg-warning-bg p-3"
              data-test-id="admin-override-banner"
            >
              <p className="font-medium text-warning">
                Je hebt deze kennisbank niet aangemaakt.
              </p>
              <p className="mt-1 text-foreground">
                Aangemaakt door <strong>{creatorName ?? '(onbekend)'}</strong>. Je
                verwijdert content van een collega — deze actie staat los van een
                eigen back-up. Vraag bij twijfel eerst even bij de aanmaker na.
              </p>
            </div>
          )}
          <p>Dit verwijdert permanent:</p>
          <ul className="list-disc list-inside space-y-1 text-muted-foreground">
            <li><strong className="text-foreground">{kbName}</strong></li>
            {itemCount !== null && <li>{itemCount} geindexeerde items</li>}
            {connectorCount > 0 && (
              <li>
                {connectorCount} connector{connectorCount !== 1 ? 's' : ''}
              </li>
            )}
            {hasGitea && <li>Docs pagina's en versiegeschiedenis</li>}
            {hasDocs && <li>Docs site</li>}
          </ul>
          <p className="text-destructive font-medium">
            Deze actie kan niet ongedaan worden gemaakt.
          </p>
          <div className="space-y-1.5 pt-2">
            <Label htmlFor="confirm-slug">
              Typ <strong>{confirmTarget}</strong> om te bevestigen
            </Label>
            <Input
              id="confirm-slug"
              value={confirmValue}
              onChange={(e) => {
                setConfirmValue(e.target.value)
                setError(null)
              }}
              placeholder={confirmTarget}
              disabled={isPending}
              autoComplete="off"
              data-test-id={isAdminOverride ? 'admin-override-confirm-input' : 'self-confirm-input'}
            />
          </div>
          {error && <p className="text-destructive text-sm">{error}</p>}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Annuleren</AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={!isMatch || isPending}
            onClick={() => deleteMutation.mutate()}
            data-test-id="delete-kb-confirm-button"
          >
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Permanent verwijderen
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
