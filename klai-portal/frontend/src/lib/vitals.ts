import { onCLS, onFCP, onINP, onLCP, onTTFB } from 'web-vitals'
import type { Metric } from 'web-vitals'
import { perfLogger } from '@/lib/logger'

interface VitalReport {
  name: string
  value: number
  rating: string
  page: string
}

const buffer: VitalReport[] = []

function handleMetric(metric: Metric) {
  buffer.push({
    name: metric.name,
    value: metric.value,
    rating: metric.rating,
    page: window.location.pathname,
  })
  perfLogger.debug(`Collected ${metric.name}`, { value: metric.value, rating: metric.rating })
}

function flushBuffer() {
  if (buffer.length === 0) return

  const payload = JSON.stringify(buffer)
  buffer.length = 0

  const blob = new Blob([payload], { type: 'application/json' })
  navigator.sendBeacon('/api/vitals', blob)
  perfLogger.debug('Flushed vitals buffer', { count: JSON.parse(payload).length })
}

export function initVitals() {
  onLCP(handleMetric)
  onFCP(handleMetric)
  onINP(handleMetric)
  onCLS(handleMetric)
  onTTFB(handleMetric)

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      flushBuffer()
    }
  })

  perfLogger.debug('Web Vitals monitoring initialized')
}
