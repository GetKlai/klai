import { useState } from 'react'
import { CheckCheck, Copy, Loader2, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import * as m from '@/paraglide/messages'
import { formatDuration } from '../-add-helpers'
import type { TranscriptionResponse } from '../-add-hooks'

interface TranscriptionResultCardsProps {
  result: TranscriptionResponse | null
  isRetrying: boolean
  onRetry: (id: string) => void
  onBack: () => void
}

export function TranscriptionResultCards({
  result,
  isRetrying,
  onRetry,
  onBack,
}: TranscriptionResultCardsProps) {
  const [copied, setCopied] = useState(false)

  function handleCopy() {
    if (!result?.text) return
    void navigator.clipboard.writeText(result.text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  if (result?.status === 'failed') {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle>{m.app_transcribe_status_failed()}</CardTitle>
          <CardDescription>{m.app_transcribe_failed_hint()}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-3 pt-2">
            <Button
              disabled={isRetrying}
              onClick={() => onRetry(result.id)}
            >
              {isRetrying ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {m.app_transcribe_processing()}
                </>
              ) : (
                <>
                  <RotateCcw className="mr-2 h-4 w-4" />
                  {m.app_transcribe_retry_button()}
                </>
              )}
            </Button>
            <Button
              variant="outline"
              disabled={isRetrying}
              onClick={onBack}
            >
              {m.app_transcribe_back()}
            </Button>
          </div>
        </CardContent>
      </Card>
    )
  }

  if (result?.status === 'transcribed' && result.text) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>{m.app_transcribe_result_title()}</CardTitle>
              {result.language && result.duration_seconds != null && (
                <CardDescription>
                  {m.app_transcribe_result_meta({
                    language: result.language.toUpperCase(),
                    duration: formatDuration(result.duration_seconds),
                  })}
                </CardDescription>
              )}
            </div>
            <Button variant="outline" size="sm" onClick={handleCopy}>
              {copied ? <CheckCheck className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <p className="text-sm whitespace-pre-wrap leading-relaxed">{result.text}</p>
        </CardContent>
      </Card>
    )
  }

  return null
}
