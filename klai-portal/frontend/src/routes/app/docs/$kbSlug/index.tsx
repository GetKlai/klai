import { createFileRoute } from '@tanstack/react-router'
import * as m from '@/paraglide/messages'

export const Route = createFileRoute('/app/docs/$kbSlug/')({
  component: NoPageSelected,
})

function NoPageSelected() {
  return (
    <div className="flex-1 flex items-center justify-center text-sm text-[var(--color-muted-foreground)]">
      {m.docs_editor_select_page()}
    </div>
  )
}
