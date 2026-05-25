import { useEffect, useMemo, useState } from 'react'
import { File, Folder, Loader2 } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { Button } from '@/components/ui/button'

const DRIVE_FILE_SCOPE = 'https://www.googleapis.com/auth/drive.file'
const GOOGLE_API_SCRIPT = 'https://apis.google.com/js/api.js'
const GOOGLE_GSI_SCRIPT = 'https://accounts.google.com/gsi/client'

type PickerMode = 'folder' | 'files'

interface GoogleProvidersResponse {
  google_drive?: {
    enabled: boolean
    client_id?: string
    picker_api_key?: string
    picker_app_id?: string
  }
}

interface PickerDocument {
  id?: string
  name?: string
  mimeType?: string
  [key: string]: unknown
}

interface PickerResponse {
  action?: string
  docs?: PickerDocument[]
  [key: string]: unknown
}

declare global {
  interface Window {
    gapi?: {
      load: (name: string, callback: () => void) => void
    }
    google?: {
      accounts?: {
        oauth2?: {
          initTokenClient: (config: {
            client_id: string
            scope: string
            callback: (response: { access_token?: string; error?: string }) => void
          }) => {
            requestAccessToken: (options?: { prompt?: string }) => void
          }
        }
      }
      picker?: {
        Action: { PICKED: string; CANCEL: string }
        DocsView: new (viewId?: string) => {
          setIncludeFolders: (enabled: boolean) => unknown
          setSelectFolderEnabled: (enabled: boolean) => unknown
          setMimeTypes: (mimeTypes: string) => unknown
          setMode?: (mode: string) => unknown
        }
        DocsViewMode?: { LIST: string }
        Feature: { MULTISELECT_ENABLED: string; NAV_HIDDEN: string }
        PickerBuilder: new () => {
          addView: (view: unknown) => unknown
          enableFeature: (feature: string) => unknown
          setAppId: (appId: string) => unknown
          setCallback: (callback: (data: PickerResponse) => void) => unknown
          setDeveloperKey: (key: string) => unknown
          setOAuthToken: (token: string) => unknown
          build: () => { setVisible: (visible: boolean) => void }
        }
        ViewId: { DOCS: string; FOLDERS: string }
      }
    }
  }
}

export interface GoogleDrivePickerResult {
  folderId: string
  folderName: string
  fileIds: string[]
}

interface PickerProps {
  kbSlug: string
  connectorId: string
  initialFolderId: string
  initialFileIds: string[]
  onConfirm: (result: GoogleDrivePickerResult) => void
  onCancel: () => void
}

let googleApiPromise: Promise<void> | null = null
let googleGsiPromise: Promise<void> | null = null

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`)
    if (existing) {
      if (existing.dataset.loaded === 'true') resolve()
      else {
        existing.addEventListener('load', () => resolve(), { once: true })
        existing.addEventListener('error', () => reject(new Error(`Kon ${src} niet laden`)), { once: true })
      }
      return
    }

    const script = document.createElement('script')
    script.src = src
    script.async = true
    script.defer = true
    script.addEventListener('load', () => {
      script.dataset.loaded = 'true'
      resolve()
    }, { once: true })
    script.addEventListener('error', () => reject(new Error(`Kon ${src} niet laden`)), { once: true })
    document.head.appendChild(script)
  })
}

async function loadGooglePickerLibraries(): Promise<void> {
  googleApiPromise ??= loadScript(GOOGLE_API_SCRIPT)
  googleGsiPromise ??= loadScript(GOOGLE_GSI_SCRIPT)
  await Promise.all([googleApiPromise, googleGsiPromise])
  await new Promise<void>((resolve, reject) => {
    if (!window.gapi) {
      reject(new Error('Google API loader is niet beschikbaar'))
      return
    }
    window.gapi.load('picker', resolve)
  })
}

function chain<T extends object>(value: unknown): T {
  return value as T
}

export function GoogleDrivePicker({
  initialFolderId,
  initialFileIds,
  onConfirm,
  onCancel,
}: PickerProps) {
  const [selectedFolderId, setSelectedFolderId] = useState<string>(initialFolderId)
  const [selectedFolderName, setSelectedFolderName] = useState('')
  const [selectedFileIds, setSelectedFileIds] = useState<string[]>(initialFileIds)
  const [selectedFileNames, setSelectedFileNames] = useState<string[]>([])
  const [isOpening, setIsOpening] = useState<PickerMode | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setSelectedFolderId(initialFolderId)
    setSelectedFileIds(initialFileIds)
  }, [initialFolderId, initialFileIds])

  const summary = useMemo(() => {
    if (selectedFolderId) return `Map: ${selectedFolderName || 'geselecteerd'}`
    if (selectedFileIds.length > 0) {
      const count = selectedFileIds.length
      if (selectedFileNames.length === 1) return selectedFileNames[0]
      return `${count} bestand${count === 1 ? '' : 'en'} geselecteerd`
    }
    return 'Nog niets geselecteerd'
  }, [selectedFolderId, selectedFolderName, selectedFileIds.length, selectedFileNames])

  async function getPickerConfig() {
    const providers = await apiFetch<GoogleProvidersResponse>('/api/oauth/providers')
    const googleDrive = providers.google_drive
    const clientId = googleDrive?.client_id || ''
    const appId = googleDrive?.picker_app_id || ''
    const apiKey = googleDrive?.picker_api_key || ''
    if (!googleDrive?.enabled || !clientId) {
      throw new Error('Google Drive OAuth is niet geconfigureerd')
    }
    if (!appId) {
      throw new Error('Google Picker app id ontbreekt')
    }
    if (!apiKey) {
      throw new Error('Google Picker API key ontbreekt')
    }
    return {
      clientId,
      appId,
      apiKey,
    }
  }

  async function openPicker(mode: PickerMode) {
    setIsOpening(mode)
    setError(null)
    try {
      const { clientId, appId, apiKey } = await getPickerConfig()
      await loadGooglePickerLibraries()
      const google = window.google
      if (!google?.accounts?.oauth2 || !google.picker) {
        throw new Error('Google Picker is niet beschikbaar')
      }

      const tokenClient = google.accounts.oauth2.initTokenClient({
        client_id: clientId,
        scope: DRIVE_FILE_SCOPE,
        callback: (response) => {
          if (response.error || !response.access_token) {
            setError(response.error || 'Google gaf geen toegangstoken terug')
            setIsOpening(null)
            return
          }
          showPicker(mode, response.access_token, appId, apiKey)
        },
      })
      tokenClient.requestAccessToken({ prompt: '' })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kon Google Picker niet openen')
      setIsOpening(null)
    }
  }

  function showPicker(mode: PickerMode, accessToken: string, appId: string, apiKey: string) {
    const pickerApi = window.google?.picker
    if (!pickerApi) return

    const view = new pickerApi.DocsView(
      mode === 'folder' ? pickerApi.ViewId.FOLDERS : pickerApi.ViewId.DOCS,
    )
    chain<{ setIncludeFolders: (enabled: boolean) => unknown }>(view.setIncludeFolders(true))
    chain<{ setSelectFolderEnabled?: (enabled: boolean) => unknown }>(
      view.setSelectFolderEnabled(mode === 'folder'),
    )
    if (mode === 'files') {
      view.setMimeTypes(
        [
          'application/vnd.google-apps.document',
          'application/vnd.google-apps.spreadsheet',
          'application/vnd.google-apps.presentation',
          'application/pdf',
        ].join(','),
      )
    }
    if (view.setMode && pickerApi.DocsViewMode?.LIST) {
      view.setMode(pickerApi.DocsViewMode.LIST)
    }

    const builder = new pickerApi.PickerBuilder()
    builder.setOAuthToken(accessToken)
    builder.setAppId(appId)
    builder.setDeveloperKey(apiKey)
    builder.addView(view)
    builder.setCallback((data) => {
      if (data.action === pickerApi.Action.PICKED) {
        const docs = data.docs || []
        if (mode === 'folder') {
          const folder = docs[0]
          const folderId = String(folder?.id || '')
          if (folderId) {
            setSelectedFolderId(folderId)
            setSelectedFolderName(String(folder?.name || ''))
            setSelectedFileIds([])
            setSelectedFileNames([])
          }
        } else {
          const files = docs.filter((doc) => doc.mimeType !== 'application/vnd.google-apps.folder')
          setSelectedFolderId('')
          setSelectedFolderName('')
          setSelectedFileIds(files.map((doc) => String(doc.id || '')).filter(Boolean))
          setSelectedFileNames(files.map((doc) => String(doc.name || '')).filter(Boolean))
        }
      }
      if (data.action === pickerApi.Action.PICKED || data.action === pickerApi.Action.CANCEL) {
        setIsOpening(null)
      }
    })
    if (mode === 'files') builder.enableFeature(pickerApi.Feature.MULTISELECT_ENABLED)

    builder.build().setVisible(true)
  }

  function selectNothing() {
    setSelectedFolderId('')
    setSelectedFolderName('')
    setSelectedFileIds([])
    setSelectedFileNames([])
  }

  return (
    <div className="rounded-lg border border-gray-200 bg-white">
      <div className="border-b border-gray-200 px-3 py-2">
        <p className="text-sm font-medium text-gray-900">Kies wat je wilt syncen</p>
        <p className="text-xs text-gray-400 mt-0.5">
          Kies een map of losse bestanden met Google Drive. Google geeft Klai alleen toegang
          tot wat je hier selecteert.
        </p>
      </div>

      <div className="space-y-3 px-3 py-3">
        <div className="rounded-md bg-gray-50 px-3 py-2 text-sm text-gray-900">
          {summary}
        </div>
        {error && (
          <div className="rounded-md border border-[var(--color-destructive)]/20 bg-[var(--color-destructive)]/5 px-3 py-2 text-xs text-[var(--color-destructive)]">
            {error}
          </div>
        )}
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => void openPicker('folder')}
            disabled={isOpening !== null}
          >
            {isOpening === 'folder' ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Folder className="h-4 w-4 mr-2" />
            )}
            Kies map
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => void openPicker('files')}
            disabled={isOpening !== null}
          >
            {isOpening === 'files' ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <File className="h-4 w-4 mr-2" />
            )}
            Kies bestanden
          </Button>
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 border-t border-gray-200 px-3 py-2">
        <Button type="button" size="sm" variant="ghost" onClick={selectNothing}>
          Leegmaken
        </Button>
        <div className="flex items-center gap-2 shrink-0">
          <Button type="button" size="sm" variant="ghost" onClick={onCancel}>
            Annuleren
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!selectedFolderId && selectedFileIds.length === 0}
            onClick={() =>
              onConfirm({
                folderId: selectedFolderId,
                folderName: selectedFolderName,
                fileIds: selectedFileIds,
              })
            }
          >
            Gebruik deze keuze
          </Button>
        </div>
      </div>
    </div>
  )
}
