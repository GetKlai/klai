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
import { API_BASE } from '@/lib/api'

interface DeleteKbModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  kbSlug: string
  kbName: string
  itemCount: number | null
  connectorCount: number
  hasGitea: boolean
  hasDocs: boolean
  token: string
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
  token,
}: DeleteKbModalProps) {
  const [confirmValue, setConfirmValue] = useState('')
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases/${kbSlug}`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) {
        let detail = 'Verwijderen mislukt'
        try {
          const data = await res.json()
          detail = data.detail ?? detail
        } catch {
          // keep default detail
        }
        throw new Error(detail)
      }
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases'] })
      void navigate({ to: '/app/knowledge' })
    },
    onError: (err: Error) => {
      setError(err.message)
    },
  })

  const isMatch = confirmValue === kbSlug
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
              Typ <strong>{kbSlug}</strong> om te bevestigen
            </Label>
            <Input
              id="confirm-slug"
              value={confirmValue}
              onChange={(e) => {
                setConfirmValue(e.target.value)
                setError(null)
              }}
              placeholder={kbSlug}
              disabled={isPending}
              autoComplete="off"
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
          >
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Permanent verwijderen
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
