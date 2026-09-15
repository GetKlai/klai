import type { LucideIcon } from 'lucide-react'
import { AlertTriangle, BookMarked, Brain, Library, MessageSquare, Mic, Sliders } from 'lucide-react'
import type { NavItem } from '@/components/layout/Sidebar'
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

/**
 * SPEC-KNOWLEDGE-ACTIVITY-001 §3: what the sidebar needs beyond the product
 * list — the caller's capabilities and the tenant's platform unlocks.
 */
export interface AppNavAccess {
  hasCapability: (capability: string) => boolean
  unlockedFeatures: string[]
  activityQueueCount?: number
}

// The review queue only makes sense for a tenant that unlocked both the widget
// channel and knowledge activity, on top of the seat capability.
const ACTIVITY_UNLOCKS = ['widgets', 'knowledge_activity']

/** Kennisgaten has its own switch: the screen is unfinished, so it stays off
    until Klai staff unlock it for a tenant. */
export function appNavGapsIsVisible(access: AppNavAccess): boolean {
  return access.hasCapability('kb.gaps') && access.unlockedFeatures.includes('knowledge_gaps')
}

/** Whether the sidebar may show (and therefore fetch for) Gesprekken. */
export function appNavActivityIsVisible(access: AppNavAccess): boolean {
  return (
    access.hasCapability('kb.activity') &&
    ACTIVITY_UNLOCKS.every((feature) => access.unlockedFeatures.includes(feature))
  )
}

export function getAppNavItems(products: string[], access: AppNavAccess): NavItem[] {
  return getAccessibleAppTools(products).map((tool) => {
    const item: NavItem = { to: tool.href, label: tool.title(), icon: tool.icon }
    if (tool.href !== '/app/knowledge') return item

    const children: NavItem[] = [
      // `end` keeps Kennisbanken from staying active on its own children's paths.
      { to: '/app/knowledge', label: m.app_nav_knowledge_bases(), icon: Library, end: true },
    ]
    if (appNavActivityIsVisible(access)) {
      children.push({
        to: '/app/knowledge/activity',
        label: m.app_nav_conversations(),
        icon: MessageSquare,
        badgeCount: access.activityQueueCount,
      })
    }
    // @MX:SPEC: SPEC-KNOWLEDGE-ACTIVITY-001 §3 — phase 2 moved the screen under /app/knowledge.
    if (appNavGapsIsVisible(access)) {
      children.push({
        to: '/app/knowledge/gaps',
        label: m.app_nav_knowledge_gaps(),
        icon: AlertTriangle,
      })
    }

    return { ...item, children }
  })
}
