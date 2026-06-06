import { createContext, useContext, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AccountMenu } from './AccountMenu'
import { ProductUpdatesPopover } from './ProductUpdatesPopover'

// Global application top bar. Rendered once by each authenticated layout
// (app + admin) above the routed <Outlet>. It is a full-width band that lines
// up with the sidebar logo header (same h-16 height + bottom border) so the
// two rails read as one continuous header.
//
// Layout: [page-injected left slot]  ...........  [topbar actions (right)]
//
// Pages inject their own contextual controls into the left slot via <TopBarLeft>
// (chat puts its config controls there). Pages with no controls leave it empty,
// which pushes the avatar to the far right.

interface TopBarSlot {
  node: HTMLElement | null
  setNode: (node: HTMLElement | null) => void
}

const TopBarSlotContext = createContext<TopBarSlot>({
  node: null,
  setNode: () => {},
})

export function TopBarSlotProvider({ children }: { children: ReactNode }) {
  const [node, setNode] = useState<HTMLElement | null>(null)
  return (
    <TopBarSlotContext.Provider value={{ node, setNode }}>
      {children}
    </TopBarSlotContext.Provider>
  )
}

export function TopBar() {
  const { setNode } = useContext(TopBarSlotContext)
  return (
    <header className="flex h-16 shrink-0 items-center gap-4 border-b border-[var(--color-sidebar-border)] bg-[var(--color-sidebar)] px-4">
      <div ref={setNode} className="flex min-w-0 flex-1 items-center" />
      <div className="flex shrink-0 items-center gap-2">
        <ProductUpdatesPopover />
        <AccountMenu />
      </div>
    </header>
  )
}

// Portals its children into the TopBar's left slot. Renders nothing until the
// TopBar has mounted and registered the slot node (one-frame delay on first
// paint, imperceptible).
export function TopBarLeft({ children }: { children: ReactNode }) {
  const { node } = useContext(TopBarSlotContext)
  return node ? createPortal(children, node) : null
}
