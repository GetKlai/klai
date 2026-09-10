import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/paraglide/messages', () => ({
  widget_chat_preview_session_error: () => 'preview-error',
}))

import { WidgetEmbedPreview, type WidgetEmbedPreviewConfig } from '../WidgetEmbedPreview'

const config = (token: string): WidgetEmbedPreviewConfig => ({
  title: 'Help',
  welcome_message: 'Hallo',
  css_variables: {},
  chat_endpoint: '/partner/v1/chat/completions',
  session_token: token,
  session_expires_at: '2099-01-01T00:00:00Z',
})

function prepareFrame(title: string) {
  const frame = screen.getByTitle<HTMLIFrameElement>(title)
  frame.contentDocument!.head.innerHTML = ''
  frame.contentDocument!.body.innerHTML = '<div id="klai-preview-host"></div>'
  return frame
}

describe('WidgetEmbedPreview', () => {
  it('mounts once with the latest config, updates drafts and disposes cleanly', async () => {
    const updateConfig = vi.fn()
    const dispose = vi.fn()
    const mountPreview = vi.fn((
      _host: HTMLElement,
      _options: { config: WidgetEmbedPreviewConfig; fetchConfig: (sessionId?: string) => Promise<WidgetEmbedPreviewConfig> },
    ) => ({ updateConfig, restart: vi.fn(), dispose }))
    const fetchConfig = vi.fn(() => Promise.resolve(config('fresh')))
    const view = render(
      <WidgetEmbedPreview widgetId="wgt_1" locale="nl" config={config('first')} fetchConfig={fetchConfig} title="Widget preview" />,
    )
    const frame = prepareFrame('Widget preview')
    Object.defineProperty(frame.contentWindow, 'KlaiWidget', {
      configurable: true,
      value: { mountPreview },
    })

    fireEvent.load(frame)
    const script = frame.contentDocument!.querySelector<HTMLScriptElement>('script:last-of-type')!
    expect(script.getAttribute('src')).toBe('/widget/klai-chat.js')
    expect(script.dataset.mode).toBe('preview')
    act(() => { script.dispatchEvent(new Event('load')) })

    const options = mountPreview.mock.calls[0][1]
    expect(options.config.session_token).toBe('first')
    await expect(options.fetchConfig('next')).resolves.toMatchObject({ session_token: 'fresh' })
    expect(fetchConfig).toHaveBeenCalledWith('next')

    view.rerender(
      <WidgetEmbedPreview widgetId="wgt_1" locale="nl" config={config('draft')} fetchConfig={fetchConfig} title="Widget preview" />,
    )
    expect(mountPreview).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(updateConfig).toHaveBeenCalledWith(config('draft')))
    view.unmount()
    expect(dispose).toHaveBeenCalledOnce()
  })

  it('shows the translated error when the widget bundle cannot mount', () => {
    render(<WidgetEmbedPreview widgetId="wgt_1" config={config('first')} fetchConfig={vi.fn()} title="Widget preview" />)
    const frame = prepareFrame('Widget preview')
    const brokenMount = vi.fn(() => { throw new Error('broken bundle') })
    Object.defineProperty(frame.contentWindow, 'KlaiWidget', {
      configurable: true,
      value: { mountPreview: brokenMount },
    })
    fireEvent.load(frame)
    act(() => { frame.contentDocument!.querySelector('script:last-of-type')!.dispatchEvent(new Event('load')) })
    expect(brokenMount).toHaveBeenCalledOnce()
    expect(screen.getByRole('alert').textContent).toBe('preview-error')
  })
})
