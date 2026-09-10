import { describe, expect, it } from 'vitest'
import { buildWidgetEmbedSnippet } from '../snippet'

describe('buildWidgetEmbedSnippet', () => {
  it('gives the customer one async script tag with the widget id and colour', () => {
    expect(buildWidgetEmbedSnippet('wgt_abc', '#270697')).toBe(
      [
        '<script async',
        '  src="https://my.getklai.com/widget/klai-chat.js"',
        '  data-widget-id="wgt_abc"',
        '  data-primary-color="#270697"',
        '></script>',
      ].join('\n'),
    )
  })

  it('leaves the colour out when the widget has none', () => {
    expect(buildWidgetEmbedSnippet('wgt_abc')).not.toContain('data-primary-color')
  })
})
