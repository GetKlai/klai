import { useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/paraglide/messages', () => ({
  admin_widgets_brand_color_label: () => 'Brand color',
  admin_widgets_background_color_label: () => 'Background color',
  widget_style_advanced_title: () => 'Advanced appearance',
  widget_style_header_background: () => 'Header background',
  widget_style_header_text: () => 'Header text',
  widget_style_header_control_background: () => 'Header controls',
  widget_style_message_font_size: () => 'Message font size',
  widget_style_message_gap: () => 'Message spacing',
  widget_style_content_padding: () => 'Content padding',
  widget_style_border_radius: () => 'Border radius',
  widget_style_window_width: () => 'Window width',
  widget_style_window_height: () => 'Window height',
  widget_style_line_height: () => 'Line height',
  widget_style_font_family: () => 'Font',
  widget_style_font_inherit: () => 'Use default',
  widget_style_font_bundled: () => 'Geist (bundled with Klai)',
  widget_style_font_system: () => 'System font',
  widget_style_font_help: () => 'The bundled font is served by Klai itself, with no external font service.',
  widget_style_input_font_size: () => 'Input font size',
  widget_style_starter_font_size: () => 'Starter font size',
}))

import { WidgetStyleOverridesFields } from '../WidgetStyleOverridesFields'

function Harness() {
  const [value, setValue] = useState<Record<string, string>>({
    '--unrelated-setting': 'keep-me',
    '--klai-header-background': '#ffffff',
  })
  return (
    <>
      <WidgetStyleOverridesFields value={value} onChange={setValue} />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  )
}

describe('WidgetStyleOverridesFields', () => {
  it('allows saving the live Voys line height of 1.65', () => {
    render(<WidgetStyleOverridesFields value={{ '--klai-message-line-height': '1.65' }} onChange={() => {}} />)
    expect(screen.getByLabelText<HTMLInputElement>('Line height').checkValidity()).toBe(true)
  })
  it('stores numeric units and clears only the edited override', () => {
    render(<Harness />)

    fireEvent.change(screen.getByLabelText(/^Message font size/), { target: { value: '18' } })
    expect(screen.getByTestId('value').textContent).toContain('"--klai-message-font-size":"18px"')
    expect(screen.getByTestId('value').textContent).toContain('"--unrelated-setting":"keep-me"')

    fireEvent.change(screen.getByLabelText('Header background'), { target: { value: '' } })
    expect(screen.getByTestId('value').textContent).not.toContain('--klai-header-background')
    expect(screen.getByTestId('value').textContent).toContain('"--unrelated-setting":"keep-me"')
  })
  it('accepts fractional px values and keeps unrelated settings when clearing the font override', () => {
    const fractional = render(
      <WidgetStyleOverridesFields
        value={{
          '--klai-message-font-size': '15.4px',
          '--klai-input-font-size': '16.5px',
          '--klai-starter-font-size': '14.3px',
          '--klai-message-gap': '18.4px',
          '--klai-content-padding': '26.4px',
        }}
        onChange={() => {}}
      />,
    )
    expect(screen.getByLabelText<HTMLInputElement>(/^Message font size/).checkValidity()).toBe(true)
    expect(screen.getByLabelText<HTMLInputElement>(/^Input font size/).checkValidity()).toBe(true)
    expect(screen.getByLabelText<HTMLInputElement>(/^Starter font size/).checkValidity()).toBe(true)
    expect(screen.getByLabelText<HTMLInputElement>(/^Message spacing/).checkValidity()).toBe(true)
    expect(screen.getByLabelText<HTMLInputElement>(/^Content padding/).checkValidity()).toBe(true)
    fractional.unmount()

    render(<Harness />)
    const font = screen.getByLabelText<HTMLSelectElement>('Font')

    fireEvent.change(font, { target: { value: '"Klai Widget Geist", system-ui, sans-serif' } })
    expect(screen.getByTestId('value').textContent).toContain('"--klai-font-family":"\\"Klai Widget Geist\\", system-ui, sans-serif"')

    fireEvent.change(font, { target: { value: 'system-ui, sans-serif' } })
    expect(screen.getByTestId('value').textContent).toContain('"--klai-font-family":"system-ui, sans-serif"')

    fireEvent.change(font, { target: { value: '' } })
    expect(screen.getByTestId('value').textContent).not.toContain('--klai-font-family')
    expect(screen.getByTestId('value').textContent).toContain('"--unrelated-setting":"keep-me"')
    expect(screen.getByTestId('value').textContent).toContain('"--klai-header-background":"#ffffff"')
  })
})
