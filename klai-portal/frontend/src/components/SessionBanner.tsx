import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '@/lib/auth'
import { RefreshCw } from 'lucide-react'
import * as m from '@/paraglide/messages'
import { authLogger } from '@/lib/logger'

// R4: Token expiry awareness. Tracks whether the access token is about to
// expire so the banner can show a non-blocking warning.
function useTokenExpiring(): boolean {
  const auth = useAuth()
  const [isExpiring, setIsExpiring] = useState(false)

  const handleExpiring = useCallback((): void => {
    authLogger.info('Access token expiring soon')
    setIsExpiring(true)
  }, [])

  const handleLoaded = useCallback((): void => {
    setIsExpiring(false)
  }, [])

  useEffect(() => {
    const removeExpiring = auth.events.addAccessTokenExpiring(handleExpiring)
    const removeLoaded = auth.events.addUserLoaded(handleLoaded)
    return () => {
      removeExpiring()
      removeLoaded()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auth.events is stable
  }, [handleExpiring, handleLoaded])

  return isExpiring
}

export function SessionBanner(): React.ReactNode {
  const isExpiring = useTokenExpiring()

  if (!isExpiring) return null

  return (
    <div className="flex items-center gap-2 bg-[var(--color-rl-cream)] px-4 py-2 text-sm text-[var(--color-foreground)]">
      <RefreshCw className="h-4 w-4 animate-spin" />
      {m.session_token_expiring()}
    </div>
  )
}
