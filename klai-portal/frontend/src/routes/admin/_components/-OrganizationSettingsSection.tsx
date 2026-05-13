import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import * as m from '@/paraglide/messages'

export function OrganizationSettingsSection() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{m.admin_settings_org_title()}</CardTitle>
        <CardDescription>
          {m.admin_settings_org_description()}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-sm text-gray-400">{m.admin_settings_placeholder()}</p>
      </CardContent>
    </Card>
  )
}
