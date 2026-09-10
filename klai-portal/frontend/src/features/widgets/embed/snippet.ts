const WIDGET_SCRIPT_URL = 'https://my.getklai.com/widget/klai-chat.js'

// Everything else (title, welcome text, colours) comes from the widget
// config on the server, so the customer's HTML never goes stale. The colour
// is the one exception: the bubble renders before the config is fetched.
export function buildWidgetEmbedSnippet(widgetId: string, primaryColor?: string): string {
  const attrs = [`  src="${WIDGET_SCRIPT_URL}"`, `  data-widget-id="${widgetId}"`]
  if (primaryColor) attrs.push(`  data-primary-color="${primaryColor}"`)
  return `<script async\n${attrs.join('\n')}\n></script>`
}
