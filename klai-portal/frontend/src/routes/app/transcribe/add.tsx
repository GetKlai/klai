import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useState } from 'react'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { ProductGuard } from '@/components/layout/ProductGuard'
import * as m from '@/paraglide/messages'
import { FileUploadForm } from './_components/-FileUploadForm'
import { RecordingForm } from './_components/-RecordingForm'
import { TranscriptionResultCards } from './_components/-TranscriptionResultCards'
import {
  useRetryTranscriptionMutation,
  useTranscribeFileMutation,
  type TranscriptionResponse,
} from './-add-hooks'
import {
  ADD_TRANSCRIBE_TABS,
  isAddTranscribeTab,
  type AddTranscribeTab,
} from './-add-helpers'

type AddTranscribeSearch = { tab?: AddTranscribeTab }

export const Route = createFileRoute('/app/transcribe/add')({
  validateSearch: (search: Record<string, unknown>): AddTranscribeSearch => ({
    tab: isAddTranscribeTab(search.tab) ? search.tab : undefined,
  }),
  component: () => (
    <ProductGuard product="scribe">
      <AddTranscribePage />
    </ProductGuard>
  ),
})

function AddTranscribePage() {
  const navigate = useNavigate({ from: '/app/transcribe/add' })
  const { tab: tabParam } = Route.useSearch()
  const activeTab: AddTranscribeTab = tabParam ?? 'record'

  const [language, setLanguage] = useState('')
  const [result, setResult] = useState<TranscriptionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [uploadResetToken, setUploadResetToken] = useState(0)

  function clearFeedback() {
    setError(null)
    setResult(null)
  }

  const transcribeMutation = useTranscribeFileMutation({
    language,
    onQueued: setResult,
    onError: setError,
    onSuccess: () => setUploadResetToken((token) => token + 1),
  })
  const retryMutation = useRetryTranscriptionMutation({
    language,
    onQueued: setResult,
    onError: setError,
  })
  const isTranscribing = transcribeMutation.isPending || retryMutation.isPending

  function selectTab(tab: AddTranscribeTab) {
    void navigate({ search: { tab: tab === 'record' ? undefined : tab } })
    setError(null)
  }

  function submitFile(file: File) {
    setError(null)
    transcribeMutation.mutate(file)
  }

  function retryTranscription(id: string) {
    setError(null)
    retryMutation.mutate(id)
  }

  return (
    <div className="mx-auto max-w-lg px-6 py-10">
      <div className="flex items-start justify-between mb-6">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.app_transcribe_add_title()}
        </h1>
        <Button type="button" variant="ghost" size="sm" onClick={() => navigate({ to: '/app/transcribe' })}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.app_transcribe_back()}
        </Button>
      </div>

      <div className="space-y-6">
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="language">{m.app_transcribe_language_label()}</Label>
            <Select
              id="language"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
              className="max-w-xs"
            >
              <option value="">{m.app_transcribe_language_auto()}</option>
              <option value="nl">{m.app_transcribe_language_nl()}</option>
              <option value="en">{m.app_transcribe_language_en()}</option>
              <option value="de">{m.app_transcribe_language_de()}</option>
              <option value="fr">{m.app_transcribe_language_fr()}</option>
            </Select>
          </div>

          <div className="flex gap-1 p-1 bg-[var(--color-muted)]/40 rounded-lg w-fit">
            {ADD_TRANSCRIBE_TABS.map((tab) => (
              <button
                key={tab}
                onClick={() => selectTab(tab)}
                className={`px-4 py-1.5 text-sm font-medium rounded-md transition-colors ${
                  activeTab === tab
                    ? 'bg-[var(--color-background)] shadow-sm text-gray-900'
                    : 'text-gray-400 hover:text-gray-900'
                }`}
              >
                {tab === 'record' ? m.app_transcribe_tab_record() : m.app_transcribe_tab_upload()}
              </button>
            ))}
          </div>

          <RecordingForm
            active={activeTab === 'record'}
            isTranscribing={isTranscribing}
            onBeforeRecord={clearFeedback}
            onRecordedFile={submitFile}
            onError={setError}
          />

          <FileUploadForm
            active={activeTab === 'upload'}
            isTranscribing={isTranscribing}
            resetToken={uploadResetToken}
            onFileReady={clearFeedback}
            onSubmit={submitFile}
            onError={setError}
          />

          {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}

          {isTranscribing && (
            <p className="text-xs text-gray-400 text-center">
              <Loader2 className="mr-2 inline h-3 w-3 animate-spin" />
              {m.app_transcribe_processing_hint()}
            </p>
          )}
        </div>

        <TranscriptionResultCards
          result={result}
          isRetrying={retryMutation.isPending}
          onRetry={retryTranscription}
          onBack={clearFeedback}
        />
      </div>
    </div>
  )
}
