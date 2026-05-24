const WIDGET_SCRIPT_URL = 'https://my.getklai.com/widget/klai-chat.js'

export function buildWidgetEmbedSnippet(
  widgetId: string,
  title?: string,
  welcomeMessage?: string,
): string {
  const attrs: string[] = [`  src="${WIDGET_SCRIPT_URL}"`]
  attrs.push(`  data-widget-id="${widgetId}"`)
  if (title) attrs.push(`  data-title="${title}"`)
  if (welcomeMessage) attrs.push(`  data-welcome="${welcomeMessage}"`)
  return `<script\n${attrs.join('\n')}\n></script>`
}
