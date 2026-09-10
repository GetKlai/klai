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
})
