import { useEffect, useRef, useState } from 'react'
import * as m from '@/paraglide/messages'

export interface WidgetEmbedPreviewConfig {
  title: string
  welcome_message: string
  css_variables: Record<string, string>
  chat_endpoint: string
  session_token: string
  session_expires_at: string
}

interface PreviewHandle {
  updateConfig: (config: WidgetEmbedPreviewConfig) => void
  dispose: () => void
}

interface PreviewApi {
  mountPreview: (
    host: HTMLElement,
    options: {
      widgetId: string
      locale?: string
      config: WidgetEmbedPreviewConfig
      fetchConfig: (sessionId?: string) => Promise<WidgetEmbedPreviewConfig>
    },
  ) => PreviewHandle
}

interface Props {
  widgetId: string
  locale?: string
  config: WidgetEmbedPreviewConfig
  fetchConfig: (sessionId?: string) => Promise<WidgetEmbedPreviewConfig>
  title: string
}

const FRAME_HTML = '<!doctype html><html><head><meta charset="utf-8"></head><body><div id="klai-preview-host"></div></body></html>'

export function WidgetEmbedPreview({ widgetId, locale, config, fetchConfig, title }: Props) {
  const frameRef = useRef<HTMLIFrameElement>(null)
  const handleRef = useRef<PreviewHandle>(null)
  const startedRef = useRef(false)
  const generationRef = useRef(0)
  const latestRef = useRef({ config, fetchConfig })
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    latestRef.current = { config, fetchConfig }
    handleRef.current?.updateConfig(config)
  }, [config, fetchConfig])

  useEffect(() => () => {
    generationRef.current += 1
    startedRef.current = false
    handleRef.current?.dispose()
    handleRef.current = null
  }, [])

  const loadWidget = () => {
    if (startedRef.current) return
    const frame = frameRef.current
    const doc = frame?.contentDocument
    const win = frame?.contentWindow
    const host = doc?.getElementById('klai-preview-host')
    const generation = ++generationRef.current
    handleRef.current?.dispose()
    handleRef.current = null
    setFailed(false)
    if (!doc || !win || !host) {
      setFailed(true)
      return
    }
    startedRef.current = true
    doc.documentElement.style.height = '100%'
    doc.body.style.height = '100%'
    doc.body.style.margin = '0'

    const script = doc.createElement('script')
    script.src = '/widget/klai-chat.js'
    script.dataset.mode = 'preview'
    script.onload = () => {
      if (generation !== generationRef.current) return
      try {
        const api = (win as Window & { KlaiWidget?: PreviewApi }).KlaiWidget
        if (!api) throw new Error('Widget preview API unavailable')
        handleRef.current = api.mountPreview(host, {
          widgetId,
          locale,
          config: latestRef.current.config,
          fetchConfig: (sessionId) => latestRef.current.fetchConfig(sessionId),
        })
      } catch {
        setFailed(true)
      }
    }
    script.onerror = () => {
      if (generation === generationRef.current) {
        setFailed(true)
      }
    }
    doc.head.appendChild(script)
  }

  return (
    <div className="relative h-full">
      <iframe
        ref={frameRef}
        title={title}
        srcDoc={FRAME_HTML}
        className="block h-full w-full border-0"
        onLoad={loadWidget}
      />
      {failed && (
        <div role="alert" className="absolute inset-0 flex items-center justify-center bg-white px-4 text-center text-sm font-medium text-gray-900">
          {m.widget_chat_preview_session_error()}
        </div>
      )}
    </div>
  )
}
