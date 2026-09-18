import { useRef, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'

interface OrgKb {
  id: number
  name: string
  slug: string
}

interface TranscriptImportResponse {
  case_id: number
  status: string
  changed: boolean
  findings_count: number
}

/**
 * Import a native Whisper transcript against an organisation KB to detect
 * content gaps (support-gap-detection.md). The transcript is analysed, never
 * published. Invalid JSON is rejected before any request; an incomplete or
 * failed analysis is never reported as success; an identical re-import is shown
 * as "nothing changed" rather than a false new finding.
 */
export function TranscriptImportDialog({ orgKbs }: { orgKbs: OrgKb[] }) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [kbSlug, setKbSlug] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [parsed, setParsed] = useState<unknown>(null)
  const [error, setError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  function reset() {
    setKbSlug('')
    setFileName(null)
    setParsed(null)
    setError(null)
    importMutation.reset()
  }

  async function handleFile(file: File) {
    setError(null)
    setParsed(null)
    setFileName(file.name)
    // Parse client-side so invalid JSON is caught before any request. The full
    // parsed object is sent as-is; nothing is truncated.
    try {
      setParsed(JSON.parse(await file.text()))
    } catch {
      setParsed(null)
      setError(m.gaps_transcript_error_invalid_json())
    }
  }

  const importMutation = useMutation({
    mutationFn: async () => {
      return apiFetch<TranscriptImportResponse>(
        `/api/app/knowledge-bases/${kbSlug}/support-cases/transcript`,
        { method: 'POST', body: JSON.stringify({ transcript: parsed }) },
      )
    },
    onSuccess: (res) => {
      // A failed analysis must not read as a successful import.
      if (res.status !== 'analyzed') {
        setError(m.gaps_transcript_error_generic())
        return
      }
      void queryClient.invalidateQueries({ queryKey: ['app-gaps'] })
      void queryClient.invalidateQueries({ queryKey: ['support-case', String(res.case_id)] })
      void queryClient.invalidateQueries({ queryKey: ['support-cases', kbSlug] })
      if (!res.changed) {
        toast.success(m.gaps_transcript_success_unchanged())
      } else {
        toast.success(m.gaps_transcript_success({ findings: String(res.findings_count) }))
      }
      setOpen(false)
      reset()
      // Open the imported case, even when it produced no gap rows: the case
      // still holds the conversation, the analysis outcome and its review.
      void navigate({
        to: '/app/knowledge/gaps/support-cases/$caseId',
        params: { caseId: String(res.case_id) },
      })
    },
    onError: (err: unknown) => {
      queryLogger.warn('Transcript import failed', { error: err })
      setError(err instanceof Error ? err.message : m.gaps_transcript_error_generic())
    },
  })

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (!next) reset()
      }}
    >
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Upload className="h-4 w-4 mr-2" />
          {m.gaps_transcript_import()}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{m.gaps_transcript_dialog_title()}</DialogTitle>
          <DialogDescription>{m.gaps_transcript_dialog_body()}</DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            importMutation.mutate()
          }}
          className="space-y-4"
        >
          <div className="space-y-1.5">
            <Label htmlFor="transcript-kb">{m.gaps_transcript_kb_label()}</Label>
            <Select
              id="transcript-kb"
              value={kbSlug}
              onChange={(e) => setKbSlug(e.target.value)}
            >
              <option value="">{m.gaps_transcript_kb_placeholder()}</option>
              {orgKbs.map((kb) => (
                <option key={kb.id} value={kb.slug}>
                  {kb.name}
                </option>
              ))}
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="transcript-file">{m.gaps_transcript_dropzone_label()}</Label>
            <input
              id="transcript-file"
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              className="block w-full text-sm text-gray-600 file:mr-3 file:rounded-md file:border file:border-gray-200 file:bg-[var(--color-card)] file:px-3 file:py-1.5 file:text-sm file:text-gray-900 hover:file:border-gray-300"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) void handleFile(file)
              }}
            />
            <p className="text-xs text-gray-600">{m.gaps_transcript_dropzone_hint()}</p>
            {fileName && !error && <p className="text-xs text-gray-600">{fileName}</p>}
          </div>
          {error && <p className="text-sm text-[var(--color-destructive)]">{error}</p>}
          <div className="flex gap-2 pt-1">
            <Button
              type="submit"
              size="sm"
              disabled={importMutation.isPending || !kbSlug || parsed === null}
            >
              {importMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  {m.gaps_transcript_submitting()}
                </>
              ) : (
                m.gaps_transcript_submit()
              )}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                setOpen(false)
                reset()
              }}
            >
              {m.gaps_transcript_cancel()}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
