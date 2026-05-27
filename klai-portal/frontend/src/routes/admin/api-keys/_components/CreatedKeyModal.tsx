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
  title?: string
  warning?: string
  description?: string
  confirmLabel?: string
}

export function CreatedKeyModal({
  apiKey,
  open,
  onConfirm,
  title,
  warning,
  description,
  confirmLabel,
}: CreatedKeyModalProps) {
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
          <AlertDialogTitle>{title ?? m.admin_api_keys_key_modal_title()}</AlertDialogTitle>
          <AlertDialogDescription className="space-y-3">
            <span className="flex items-center gap-2 text-[var(--color-destructive)] font-medium">
              <AlertTriangle className="h-4 w-4 shrink-0" />
              {warning ?? m.admin_api_keys_key_modal_warning()}
            </span>
            <span className="block">
              {description ?? m.admin_api_keys_key_modal_description()}
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="my-4 flex items-center gap-2 rounded-md border border-gray-200 bg-[var(--color-card)] p-3">
          <code className="flex-1 break-all text-xs font-mono text-gray-900">
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
            {confirmLabel ?? m.admin_api_keys_key_modal_confirm()}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
