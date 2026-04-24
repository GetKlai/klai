import type { ComponentType } from 'react'
import { FileUp, Link2, Type, Globe, FileText } from 'lucide-react'
import {
  SiGithub,
  SiNotion,
  SiGoogledrive,
  SiAirtable,
  SiConfluence,
  SiGoogledocs,
  SiGooglesheets,
  SiGoogleslides,
  SiYoutube,
} from '@icons-pack/react-simple-icons'
import * as m from '@/paraglide/messages'

export type SourceGroup = 'upload' | 'connector'

export type UploadType = 'file' | 'url' | 'youtube' | 'text'

export type ConnectorSourceType =
  | 'github'
  | 'notion'
  | 'google_drive'
  | 'google_docs'
  | 'google_sheets'
  | 'google_slides'
  | 'airtable'
  | 'confluence'
  | 'ms_docs'
  | 'web_crawler'

export type SourceType = UploadType | ConnectorSourceType

export interface SourceTypeMeta {
  type: SourceType
  group: SourceGroup
  label: () => string
  subtitle: () => string
  Icon: ComponentType<{ className?: string; size?: number | string }>
  available: boolean
  /** For connector types: builds the deep-link URL. Upload types (file/url/text): undefined. */
  routeTo?: (kbSlug: string) => string
}

export const SOURCE_TYPES: SourceTypeMeta[] = [
  // -- Upload group ---------------------------------------------------------
  {
    type: 'file',
    group: 'upload',
    label: m.knowledge_add_source_file_label,
    subtitle: m.knowledge_add_source_file_subtitle,
    Icon: FileUp,
    available: true,
  },
  {
    type: 'url',
    group: 'upload',
    label: m.knowledge_add_source_url_label,
    subtitle: m.knowledge_add_source_url_subtitle,
    Icon: Link2,
    available: true,
  },
  {
    type: 'youtube',
    group: 'upload',
    label: m.knowledge_add_source_youtube_label,
    subtitle: m.knowledge_add_source_youtube_subtitle,
    Icon: SiYoutube,
    available: true,
  },
  {
    type: 'text',
    group: 'upload',
    label: m.knowledge_add_source_text_label,
    subtitle: m.knowledge_add_source_text_subtitle,
    Icon: Type,
    available: true,
  },

  // -- Connector group -------------------------------------------------------
  {
    type: 'github',
    group: 'connector',
    label: m.admin_connectors_type_github,
    subtitle: m.knowledge_add_source_connector_subtitle_github,
    Icon: SiGithub,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=github`,
  },
  {
    type: 'notion',
    group: 'connector',
    label: m.admin_connectors_type_notion,
    subtitle: m.knowledge_add_source_connector_subtitle_notion,
    Icon: SiNotion,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=notion`,
  },
  {
    type: 'google_drive',
    group: 'connector',
    label: m.admin_connectors_type_google_drive,
    subtitle: m.knowledge_add_source_connector_subtitle_google_drive,
    Icon: SiGoogledrive,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=google_drive`,
  },
  {
    type: 'google_docs',
    group: 'connector',
    label: m.admin_connectors_type_google_docs,
    subtitle: m.knowledge_add_source_connector_subtitle_google_docs,
    Icon: SiGoogledocs,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=google_docs`,
  },
  {
    type: 'google_sheets',
    group: 'connector',
    label: m.admin_connectors_type_google_sheets,
    subtitle: m.knowledge_add_source_connector_subtitle_google_sheets,
    Icon: SiGooglesheets,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=google_sheets`,
  },
  {
    type: 'google_slides',
    group: 'connector',
    label: m.admin_connectors_type_google_slides,
    subtitle: m.knowledge_add_source_connector_subtitle_google_slides,
    Icon: SiGoogleslides,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=google_slides`,
  },
  {
    type: 'airtable',
    group: 'connector',
    label: m.admin_connectors_type_airtable,
    subtitle: m.knowledge_add_source_connector_subtitle_airtable,
    Icon: SiAirtable,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=airtable`,
  },
  {
    type: 'confluence',
    group: 'connector',
    label: m.admin_connectors_type_confluence,
    subtitle: m.knowledge_add_source_connector_subtitle_confluence,
    Icon: SiConfluence,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=confluence`,
  },
  {
    type: 'ms_docs',
    group: 'connector',
    label: m.admin_connectors_type_ms_docs,
    subtitle: m.knowledge_add_source_connector_subtitle_ms_docs,
    Icon: FileText,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=ms_docs`,
  },
  {
    type: 'web_crawler',
    group: 'connector',
    label: m.admin_connectors_type_website,
    subtitle: m.knowledge_add_source_connector_subtitle_web_crawler,
    Icon: Globe,
    available: true,
    routeTo: (kbSlug) => `/app/knowledge/${kbSlug}/add-connector?type=web_crawler`,
  },
]
