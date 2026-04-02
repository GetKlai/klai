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
import * as m from '@/paraglide/messages'

interface DeletePageModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  pageTitle: string
  onConfirm: () => void
  isPending: boolean
}

export function DeletePageModal({
  open,
  onOpenChange,
  pageTitle,
  onConfirm,
  isPending,
}: DeletePageModalProps) {
  function handleOpenChange(next: boolean) {
    if (isPending) return
    onOpenChange(next)
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="h-5 w-5" />
            {m.docs_page_delete_title()}
          </AlertDialogTitle>
        </AlertDialogHeader>

        <div className="space-y-3 text-sm">
          <p>{m.docs_page_delete_description()}</p>
          <p className="text-muted-foreground">
            <strong className="text-foreground">{pageTitle}</strong>
          </p>
          <p className="text-destructive font-medium">
            {m.docs_page_delete_warning()}
          </p>
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>
            {m.docs_tree_cancel()}
          </AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={isPending}
            onClick={onConfirm}
          >
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {m.docs_page_delete_confirm()}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
