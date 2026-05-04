import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useMutation } from '@tanstack/react-query'
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
import { deprovisionLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'

interface DeleteOrgModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  orgSlug: string
  orgName: string
}

// @MX:NOTE: Tier 1 confirmation pattern (AlertDialog + type-slug-to-confirm).
// @MX:SPEC: SPEC-INFRA-TENANT-DELETE-001 Phase 10 R10
export function DeleteOrgModal({
  open,
  onOpenChange,
  orgSlug,
  orgName,
}: DeleteOrgModalProps) {
  const [confirmValue, setConfirmValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const deleteMutation = useMutation({
    mutationFn: async () => {
      await apiFetch('/api/admin/org/me', { method: 'DELETE' })
    },
    onSuccess: () => {
      deprovisionLogger.info('Org deletion queued', { orgSlug })
      void navigate({ to: '/admin/deprovisioning-status' })
    },
    onError: (err: Error) => {
      deprovisionLogger.error('Org deletion request failed', { orgSlug, error: err.message })
      if (err.message.includes('already_deprovisioning')) {
        setError(m.delete_org_modal_error_conflict())
      } else {
        setError(m.delete_org_modal_error_generic())
      }
    },
  })

  const isMatch = confirmValue === orgSlug
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
          <AlertDialogTitle className="flex items-center gap-2 text-[var(--color-destructive)]">
            <AlertTriangle className="h-5 w-5" />
            {m.delete_org_modal_title()}
          </AlertDialogTitle>
        </AlertDialogHeader>

        <div className="space-y-3 text-sm">
          <p>{m.delete_org_modal_intro()}</p>
          <ul className="list-disc list-inside space-y-1 text-[var(--color-muted-foreground)]">
            <li>
              <strong className="text-[var(--color-foreground)]">{orgName}</strong>{' '}
              {m.delete_org_modal_item_org()}
            </li>
            <li>{m.delete_org_modal_item_members()}</li>
            <li>{m.delete_org_modal_item_kbs()}</li>
            <li>{m.delete_org_modal_item_integrations()}</li>
            <li className="text-[var(--color-muted-foreground)] italic">
              {m.delete_org_modal_item_billing()}
            </li>
          </ul>
          <p className="text-[var(--color-destructive)] font-medium">
            {m.delete_org_modal_warning()}
          </p>
          <div className="space-y-1.5 pt-2">
            <Label htmlFor="confirm-org-slug">
              {m.delete_org_modal_confirm_label({ slug: orgSlug })}
            </Label>
            <Input
              id="confirm-org-slug"
              value={confirmValue}
              onChange={(e) => {
                setConfirmValue(e.target.value)
                setError(null)
              }}
              placeholder={m.delete_org_modal_confirm_placeholder()}
              disabled={isPending}
              autoComplete="off"
            />
          </div>
          {error && (
            <p className="text-[var(--color-destructive)] text-sm">{error}</p>
          )}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>
            {m.delete_org_modal_cancel()}
          </AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={!isMatch || isPending}
            onClick={() => deleteMutation.mutate()}
          >
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isPending ? m.delete_org_modal_submitting() : m.delete_org_modal_submit()}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
