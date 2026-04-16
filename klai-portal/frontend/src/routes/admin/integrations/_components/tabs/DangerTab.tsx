import { useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { IntegrationDetailResponse } from '../../-types'
import { useRevokeIntegration } from '../../-hooks'
import { RevokeConfirmDialog } from '../RevokeConfirmDialog'

interface DangerTabProps {
  integration: IntegrationDetailResponse
}

export function DangerTab({ integration }: DangerTabProps) {
  const revokeMutation = useRevokeIntegration(String(integration.id))
  const [showDialog, setShowDialog] = useState(false)

  const isRevoked = integration.active === false

  function handleRevoke() {
    revokeMutation.mutate(undefined, {
      onSuccess: () => {
        setShowDialog(false)
        toast.success(m.admin_integrations_success_revoked())
      },
    })
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-medium text-[var(--color-destructive)] mb-2">
          {m.admin_integrations_revoke_section_title()}
        </h2>
        <p className="text-sm text-[var(--color-muted-foreground)] mb-4">
          {m.admin_integrations_revoke_section_description()}
        </p>
        <Button
          type="button"
          variant="destructive"
          size="sm"
          disabled={isRevoked}
          onClick={() => setShowDialog(true)}
        >
          {m.admin_integrations_revoke_button()}
        </Button>
      </div>

      <RevokeConfirmDialog
        open={showDialog}
        isPending={revokeMutation.isPending}
        onConfirm={handleRevoke}
        onCancel={() => setShowDialog(false)}
      />
    </div>
  )
}
