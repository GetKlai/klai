import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Loader2, RotateCcw } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { ApiKeyDetailResponse } from '../../-types'
import { useRotateApiKey } from '../../-hooks'
import { CreatedKeyModal } from '../CreatedKeyModal'

interface Props {
  apiKey: ApiKeyDetailResponse
}

export function RotationTab({ apiKey }: Props) {
  const navigate = useNavigate()
  const rotateMutation = useRotateApiKey(String(apiKey.id))
  const [rotatedKey, setRotatedKey] = useState<string | null>(null)
  const [rotatedKeyId, setRotatedKeyId] = useState<string | null>(null)

  function handleRotate() {
    rotateMutation.mutate(undefined, {
      onSuccess: (result) => {
        setRotatedKey(result.api_key)
        setRotatedKeyId(String(result.id))
        toast.success(m.admin_api_keys_rotate_success())
      },
    })
  }

  function handleRotateModalConfirm() {
    const nextId = rotatedKeyId
    setRotatedKey(null)
    setRotatedKeyId(null)
    if (nextId) {
      void navigate({ to: '/admin/api-keys/$id', params: { id: nextId } })
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-medium text-gray-900 mb-2">
          {m.admin_api_keys_rotate_section_title()}
        </h2>
        <p className="text-sm text-gray-400 mb-4">
          {apiKey.rotated_to_key_id
            ? m.admin_api_keys_rotate_pending_description()
            : m.admin_api_keys_rotate_section_description()}
        </p>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleRotate}
          disabled={rotateMutation.isPending || Boolean(apiKey.rotated_to_key_id)}
        >
          {rotateMutation.isPending ? (
            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
          ) : (
            <RotateCcw className="h-4 w-4 mr-2" />
          )}
          {m.admin_api_keys_rotate_button()}
        </Button>
        {rotateMutation.error && (
          <p className="mt-3 text-sm text-[var(--color-destructive)]">
            {rotateMutation.error instanceof Error
              ? rotateMutation.error.message
              : m.admin_shared_error_generic()}
          </p>
        )}
      </div>

      {apiKey.rotated_from_key_id && (
        <p className="text-sm text-gray-400">
          {m.admin_api_keys_rotated_from_notice()}
        </p>
      )}

      <CreatedKeyModal
        apiKey={rotatedKey ?? ''}
        open={rotatedKey !== null}
        title={m.admin_api_keys_rotate_modal_title()}
        warning={m.admin_api_keys_key_modal_warning()}
        description={m.admin_api_keys_rotate_modal_description()}
        confirmLabel={m.admin_api_keys_rotate_modal_confirm()}
        onConfirm={handleRotateModalConfirm}
      />
    </div>
  )
}
