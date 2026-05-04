/**
 * Singleton navigate function for use outside React component trees.
 *
 * Registered by main.tsx after the router is created. apiFetch uses this to
 * redirect to /tenant-deleted when a 403 with error="tenant_deleting" is received.
 */

// @MX:NOTE: Registered from main.tsx; allows apiFetch to navigate without being inside React.

type NavigateFn = (to: string) => void

let _navigate: NavigateFn | null = null
let _clearQueries: (() => void) | null = null

export function registerNavigateSingleton(navigate: NavigateFn, clearQueries: () => void) {
  _navigate = navigate
  _clearQueries = clearQueries
}

export function navigateTo(to: string) {
  if (_navigate) {
    _navigate(to)
  } else {
    // Fallback for edge cases before router is ready
    window.location.replace(to)
  }
}

export function clearAllQueries() {
  if (_clearQueries) {
    _clearQueries()
  }
}
