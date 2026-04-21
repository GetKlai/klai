import { useState, useEffect } from 'react'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import * as m from '@/paraglide/messages'
import type { ApiKeyDetailResponse } from '../../-types'
import { useUpdateApiKey } from '../../-hooks'

interface Props {
  apiKey: ApiKeyDetailResponse
}

export function RateLimitTab({ apiKey }: Props) {
  const updateMutation = useUpdateApiKey(String(apiKey.id))
  const [rateLimit, setRateLimit] = useState(apiKey.rate_limit_rpm)

  useEffect(() => {
    setRateLimit(apiKey.rate_limit_rpm)
  }, [apiKey.rate_limit_rpm])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    updateMutation.mutate(
      { rate_limit_rpm: rateLimit },
      {
        onSuccess: () => toast.success(m.admin_shared_success_updated()),
      },
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <section className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="rate-limit">{m.admin_api_keys_field_rate_limit()}</Label>
          <div className="flex items-center gap-2">
            <Input
              id="rate-limit"
              type="number"
              min={10}
              max={600}
              value={rateLimit}
              onChange={(e) => setRateLimit(Number(e.target.value))}
              className="max-w-[8rem]"
            />
            <span className="text-sm text-[var(--color-muted-foreground)]">
              {m.admin_api_keys_rate_limit_unit()}
            </span>
          </div>
        </div>
      </section>

      {updateMutation.error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {updateMutation.error instanceof Error
            ? updateMutation.error.message
            : m.admin_shared_error_generic()}
        </p>
      )}

      <div className="pt-2">
        <Button type="submit" disabled={updateMutation.isPending}>
          {updateMutation.isPending && (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          )}
          {m.admin_shared_save()}
        </Button>
      </div>
    </form>
  )
}
