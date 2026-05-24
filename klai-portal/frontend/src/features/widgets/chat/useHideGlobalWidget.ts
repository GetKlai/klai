import { useEffect } from 'react'

export function useHideGlobalWidget(styleId: string, selectors: string) {
  useEffect(() => {
    let style = document.getElementById(styleId) as HTMLStyleElement | null
    if (!style) {
      style = document.createElement('style')
      style.id = styleId
      style.textContent = `${selectors} { display: none !important; }`
      document.head.appendChild(style)
    }
    return () => {
      style?.remove()
    }
  }, [styleId, selectors])
}
