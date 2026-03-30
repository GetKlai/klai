import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouterState } from '@tanstack/react-router'
import { createDriver, destroyDriver, type DriverInstance } from './driver'
import { genericIntro } from './intro-steps'
import { routeIntros, routeSteps } from './steps'
import type { HelpStep, HelpPageIntro } from './types'
import { STORAGE_KEYS } from '@/lib/storage'
import { helpLogger } from '@/lib/logger'

export function useHelp() {
  const [enabled, setEnabled] = useState<boolean>(
    () => localStorage.getItem(STORAGE_KEYS.helpEnabled) === 'true',
  )
  const [showIntro, setShowIntro] = useState(false)

  const enabledRef = useRef(enabled)
  useEffect(() => {
    enabledRef.current = enabled
  }, [enabled])

  const driverRef = useRef<DriverInstance | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)
  const pathname = useRouterState({ select: (s) => s.location.pathname })

  const destroyActive = useCallback(() => {
    if (cleanupRef.current) {
      cleanupRef.current()
      cleanupRef.current = null
    }
    destroyDriver(driverRef.current)
    driverRef.current = null
  }, [])

  const registerHoverListeners = useCallback(
    (steps: HelpStep[]) => {
      if (cleanupRef.current) cleanupRef.current()

      const handlers: Array<{ el: Element; enter: () => void; leave: () => void }> = []

      for (const step of steps) {
        const elements = document.querySelectorAll(`[data-help-id="${step.id}"]`)
        for (const el of elements) {
          const enter = () => {
            destroyDriver(driverRef.current)
            driverRef.current = createDriver()
            driverRef.current.highlight({
              element: el as HTMLElement,
              popover: {
                title: step.title(),
                description: step.description(),
              },
            })
          }
          const leave = () => {
            destroyDriver(driverRef.current)
            driverRef.current = null
          }
          el.addEventListener('mouseenter', enter)
          el.addEventListener('mouseleave', leave)
          handlers.push({ el, enter, leave })
        }
      }

      cleanupRef.current = () => {
        for (const { el, enter, leave } of handlers) {
          el.removeEventListener('mouseenter', enter)
          el.removeEventListener('mouseleave', leave)
        }
        handlers.length = 0
      }
    },
    [],
  )

  const dismissIntro = useCallback(() => {
    setShowIntro(false)
    const steps = routeSteps[pathname] ?? []
    if (steps.length > 0) {
      registerHoverListeners(steps)
      helpLogger.debug('Help hover listeners registered after intro dismiss', { pathname })
    }
  }, [pathname, registerHoverListeners])

  // When disabled: clean up everything
  useEffect(() => {
    if (!enabled) {
      destroyActive()
      setShowIntro(false)
    }
  }, [enabled, destroyActive])

  // On route change (not on enable): show new page intro and re-register listeners
  useEffect(() => {
    if (!enabledRef.current) return
    destroyActive()
    setShowIntro(true)
    helpLogger.debug('Route changed, showing intro for new page', { pathname })
  }, [pathname, destroyActive])

  const toggle = useCallback(() => {
    setEnabled((prev) => {
      const next = !prev
      localStorage.setItem(STORAGE_KEYS.helpEnabled, String(next))
      if (next) {
        helpLogger.info('Help enabled', { pathname })
        setShowIntro(true)
      } else {
        destroyActive()
        setShowIntro(false)
        helpLogger.info('Help disabled')
      }
      return next
    })
  }, [pathname, destroyActive])

  useEffect(() => {
    return () => { destroyActive() }
  }, [destroyActive])

  const introContent: HelpPageIntro = routeIntros[pathname] ?? genericIntro

  return { enabled, toggle, showIntro, dismissIntro, introContent }
}
