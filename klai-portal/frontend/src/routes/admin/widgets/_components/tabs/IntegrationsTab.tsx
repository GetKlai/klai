import { ExternalLink, MessageSquareText } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import type { WidgetDetailResponse } from '../../-types'

interface Props {
  widget: WidgetDetailResponse
}

const HUBSPOT_HELP_DESK_URL = 'https://app.hubspot.com/help-desk/147785398/views/all/open'

export function IntegrationsTab({ widget }: Props) {
  return (
    <section className="space-y-6">
      <div className="space-y-1.5">
        <h2 className="text-lg font-display-bold text-gray-900">
          {m.admin_widgets_integrations_title()}
        </h2>
        <p className="max-w-2xl text-sm text-gray-400">
          {m.admin_widgets_integrations_intro({ name: widget.name })}
        </p>
      </div>

      <div className="grid gap-3">
        <article className="rounded-lg border border-gray-200 bg-white p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex min-w-0 gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[#ff7a59]/10 text-[#ff7a59]">
                <MessageSquareText className="h-5 w-5" />
              </div>
              <div className="min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-gray-900">HubSpot</h3>
                  <Badge variant="success">
                    {m.admin_widgets_integrations_status_sandbox_connected()}
                  </Badge>
                </div>
                <p className="max-w-2xl text-sm text-gray-500">
                  {m.admin_widgets_integrations_hubspot_description()}
                </p>
                <dl className="grid gap-2 pt-1 text-xs text-gray-400 sm:grid-cols-3">
                  <div>
                    <dt className="font-medium text-gray-500">
                      {m.admin_widgets_integrations_target_label()}
                    </dt>
                    <dd>HubSpot Help Desk</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-gray-500">
                      {m.admin_widgets_integrations_channel_label()}
                    </dt>
                    <dd>Klai Webchat Support</dd>
                  </div>
                  <div>
                    <dt className="font-medium text-gray-500">
                      {m.admin_widgets_integrations_mode_label()}
                    </dt>
                    <dd>{m.admin_widgets_integrations_mode_realtime()}</dd>
                  </div>
                </dl>
              </div>
            </div>
            <Button type="button" variant="secondary" size="sm" asChild>
              <a href={HUBSPOT_HELP_DESK_URL} target="_blank" rel="noreferrer">
                {m.admin_widgets_integrations_open_hubspot()}
                <ExternalLink className="h-4 w-4" />
              </a>
            </Button>
          </div>
        </article>
      </div>
    </section>
  )
}
