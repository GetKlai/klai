import type { LucideIcon } from 'lucide-react'
import { BookMarked, Brain, MessageSquare, Mic, Sliders } from 'lucide-react'
import * as m from '@/paraglide/messages'

interface AppTool {
  href: string
  title: () => string
  description: () => string
  icon: LucideIcon
  helpId: string
  requiredProducts?: string[]
}

export const APP_TOOLS: AppTool[] = [
  {
    title: m.app_tool_chat_title,
    description: m.app_tool_chat_description,
    icon: MessageSquare,
    href: '/app/chat',
    helpId: 'home-tool-chat',
    requiredProducts: ['chat'],
  },
  {
    title: m.instructions_page_title,
    description: m.instructions_page_subtitle,
    icon: Sliders,
    href: '/app/instructions',
    helpId: 'home-tool-instructions',
  },
  {
    title: m.app_tool_transcribe_title,
    description: m.app_tool_transcribe_description,
    icon: Mic,
    href: '/app/transcribe',
    helpId: 'home-tool-transcribe',
    requiredProducts: ['scribe'],
  },
  {
    title: m.app_tool_knowledge_title,
    description: m.app_tool_knowledge_description,
    icon: Brain,
    href: '/app/knowledge',
    helpId: 'home-tool-knowledge',
    requiredProducts: ['knowledge'],
  },
  {
    title: m.app_tool_docs_title,
    description: m.app_tool_docs_description,
    icon: BookMarked,
    href: '/app/docs',
    helpId: 'home-tool-docs',
    requiredProducts: ['docs'],
  },
]

export function getAccessibleAppTools(products: string[]): AppTool[] {
  return APP_TOOLS.filter((tool) => {
    if (!tool.requiredProducts) return true
    return tool.requiredProducts.some((product) => products.includes(product))
  })
}
