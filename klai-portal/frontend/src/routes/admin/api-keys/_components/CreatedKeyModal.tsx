import { useState } from 'react'
import { Copy, Check, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
} from '@/components/ui/alert-dialog'
import * as m from '@/paraglide/messages'

interface CreatedKeyModalProps {
  apiKey: string
  open: boolean
  onConfirm: () => void
}

export function CreatedKeyModal({ apiKey, open, onConfirm }: CreatedKeyModalProps) {
  const [copied, setCopied] = useState(false)

  async function handleCopy() {
    await navigator.clipboard.writeText(apiKey)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <AlertDialog open={open}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{m.admin_integrations_key_modal_title()}</AlertDialogTitle>
          <AlertDialogDescription className="space-y-3">
            <span className="flex items-center gap-2 text-[var(--color-destructive)] font-medium">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {m.admin_integrations_key_modal_warning()}
            </span>
            <span className="block">
              {m.admin_integrations_key_modal_description()}
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="my-4 flex items-center gap-2 rounded-md border border-[var(--color-border)] bg-[var(--color-card)] p-3">
          <code className="flex-1 break-all text-xs font-mono text-[var(--color-foreground)]">
            {apiKey}
          </code>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => void handleCopy()}
            className="shrink-0"
          >
            {copied ? (
              <Check className="h-4 w-4 text-[var(--color-success)]" />
            ) : (
              <Copy className="h-4 w-4" />
            )}
          </Button>
        </div>

        <AlertDialogFooter>
          <Button onClick={onConfirm}>
            {m.admin_integrations_key_modal_confirm()}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
