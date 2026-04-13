import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import * as m from '@/paraglide/messages'

interface RevokeConfirmDialogProps {
  open: boolean
  isPending: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function RevokeConfirmDialog({
  open,
  isPending,
  onConfirm,
  onCancel,
}: RevokeConfirmDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={(o) => { if (!o) onCancel() }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {m.admin_integrations_revoke_title()}
          </AlertDialogTitle>
          <AlertDialogDescription>
            {m.admin_integrations_revoke_description()}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>
            {m.admin_users_cancel()}
          </AlertDialogCancel>
          <AlertDialogAction asChild>
            <Button
              className="bg-[var(--color-destructive)] text-white hover:bg-[var(--color-destructive)]/90"
              onClick={onConfirm}
              disabled={isPending}
            >
              {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {m.admin_integrations_revoke_confirm()}
            </Button>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
