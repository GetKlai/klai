import { FolderOpen } from 'lucide-react'
import { INDENT_WIDTH, DEFAULT_ICON, stripMdExt } from '@/lib/kb-editor/tree-utils'
import type { NavNode } from '@/lib/kb-editor/tree-utils'

interface NavItemOverlayProps {
  node: NavNode
  projectedDepth: number
  activeTitle?: string
  activePath?: string | null
}

export function NavItemOverlay({ node, projectedDepth, activeTitle, activePath }: NavItemOverlayProps) {
  const displayTitle =
    activePath && activePath === stripMdExt(node.path)
      ? (activeTitle ?? node.title)
      : node.title

  const isDir = node.type === 'dir'
  const paddingLeft = projectedDepth * INDENT_WIDTH + 8

  return (
    <div
      className="flex items-center gap-1.5 rounded text-xs font-medium bg-[var(--color-card)] border border-[var(--color-border)] shadow-md py-1 pr-2 text-[var(--color-foreground)]"
      style={{ paddingLeft: `${paddingLeft}px` }}
    >
      {isDir
        ? <FolderOpen size={13} className="shrink-0" />
        : <span className="shrink-0 text-sm leading-none">{node.icon ?? DEFAULT_ICON}</span>
      }
      <span className="truncate">{displayTitle}</span>
    </div>
  )
}
