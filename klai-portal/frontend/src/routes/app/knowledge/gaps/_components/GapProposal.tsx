// "Voorstel" section on the cluster detail page (knowledge-gap-to-article phase 1).
// The owner reads what to write and where, copies the example text into the
// source system by hand, and may close the cluster with one of two final
// decisions. Nothing is written to a knowledge base from here.
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Markdown from 'react-markdown'
import { Check, Copy } from 'lucide-react'
import { toast } from 'sonner'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { formatRelativeTime } from '../-gap-types'
import {
  clusterPath,
  isWebUrl,
  notDecidableReason,
  type ClusterDecision,
  type ClusterProposal,
  type ClusterRow,
} from '../-cluster-types'
import { DecisionLine } from './DecisionLine'

function errorDetail(err: unknown, fallback: string): string {
  return err instanceof ApiError && err.detail ? err.detail : fallback
}

function ArticleRef({ title, url }: { title: string; url: string | null }) {
  return isWebUrl(url) ? (
    <a href={url} target="_blank" rel="noopener noreferrer" className="font-medium text-rl-dark underline">
      {title}
    </a>
  ) : (
    <span className="font-medium">{title}</span>
  )
}

export function GapProposal({ cluster }: { cluster: ClusterRow }) {
  const clusterKey = cluster.cluster_key
  const queryClient = useQueryClient()
  const [copied, setCopied] = useState<'ok' | 'failed' | null>(null)
  const [pending, setPending] = useState<ClusterDecision | null>(null)
  const queryKey = ['app-gap-proposal', clusterKey]

  const proposalQuery = useQuery<ClusterProposal | null>({
    queryKey,
    queryFn: async () => {
      try {
        return await apiFetch<ClusterProposal>(`${clusterPath(clusterKey)}/proposal`)
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return null
        queryLogger.warn('Cluster proposal fetch failed', { error: err })
        throw err
      }
    },
    retry: false,
  })

  const createMutation = useMutation({
    mutationFn: () => apiFetch<ClusterProposal>(`${clusterPath(clusterKey)}/proposal`, { method: 'POST' }),
    onSuccess: (created) => {
      queryClient.setQueryData(queryKey, created)
      // The list shows a marker when a proposal exists.
      void queryClient.invalidateQueries({ queryKey: ['app-gap-clusters'] })
    },
  })

  const decideMutation = useMutation({
    mutationFn: (decision: ClusterDecision) =>
      apiFetch<unknown>(`${clusterPath(clusterKey)}/decision`, {
        method: 'POST',
        body: JSON.stringify({ decision }),
      }),
    onError: (err: unknown) => {
      queryLogger.warn('Cluster decision failed', { error: err })
      toast.error(errorDetail(err, m.gaps_decision_failed()))
    },
    // A 409 means someone else decided first: refresh either way so the page
    // shows the decision that stands.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey })
      void queryClient.invalidateQueries({ queryKey: ['app-gap-clusters'] })
    },
  })

  const proposal = proposalQuery.data ?? null
  const decided = proposal && proposal.decision !== 'none' ? proposal : cluster.decision !== 'none' ? cluster : null

  async function copyExample(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setCopied('ok')
    } catch {
      setCopied('failed')
    }
  }

  return (
    <section className="space-y-4" aria-labelledby="gap-proposal-heading">
      <h2 id="gap-proposal-heading" className="text-sm font-semibold text-gray-900">
        {m.gaps_proposal_heading()}
      </h2>

      {proposalQuery.isError && (
        <p role="alert" className="text-sm text-[var(--color-destructive)]">
          {errorDetail(proposalQuery.error, m.gaps_proposal_load_failed())}
        </p>
      )}

      {cluster.decidable && proposalQuery.isSuccess && !proposal && !decided && (
        <div className="space-y-2">
          <Button size="sm" disabled={createMutation.isPending} onClick={() => createMutation.mutate()}>
            {createMutation.isPending ? m.gaps_proposal_creating() : m.gaps_proposal_create()}
          </Button>
          {createMutation.isError && (
            <p role="alert" className="text-sm text-[var(--color-destructive)]">
              {errorDetail(createMutation.error, m.gaps_proposal_create_failed())}
            </p>
          )}
        </div>
      )}

      {proposal && (
        <div className="space-y-4">
          <div>
            <h3 className="mb-1 text-sm font-medium text-gray-900">{m.gaps_proposal_missing_heading()}</h3>
            <p className="text-sm text-gray-900">{proposal.missing}</p>
          </div>

          <div>
            <h3 className="mb-1 text-sm font-medium text-gray-900">{m.gaps_proposal_where_heading()}</h3>
            {proposal.where_kind === 'existing' ? (
              <>
                <p className="text-sm">
                  <ArticleRef title={proposal.where_title} url={proposal.where_url} />
                </p>
                {proposal.where_section && <p className="text-sm text-gray-600">{proposal.where_section}</p>}
              </>
            ) : (
              <>
                <p className="text-sm text-gray-900">
                  {m.gaps_proposal_where_new({ section: proposal.where_section ?? '' })}
                </p>
                <p className="text-sm text-gray-600">
                  {m.gaps_proposal_where_nearest()}{' '}
                  <ArticleRef title={proposal.where_title} url={proposal.where_url} />
                </p>
              </>
            )}
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between gap-2">
              <h3 className="text-sm font-medium text-gray-900">{m.gaps_proposal_example_heading()}</h3>
              <Button variant="outline" size="sm" onClick={() => void copyExample(proposal.example_text)}>
                {copied === 'ok' ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
                {m.gaps_proposal_copy()}
              </Button>
            </div>
            <div className="rounded-lg border border-gray-200 p-3 text-sm text-gray-900 space-y-1 [&_h1]:font-semibold [&_h1]:mt-3 [&_h2]:font-semibold [&_h2]:mt-3 [&_h3]:font-semibold [&_h3]:mt-2 [&_ul]:list-disc [&_ul]:pl-4 [&_ol]:list-decimal [&_ol]:pl-4 [&_li]:mt-0.5 [&_strong]:font-semibold [&_p]:leading-relaxed">
              <Markdown>{proposal.example_text}</Markdown>
            </div>
            <span role="status" aria-live="polite" className="text-xs text-gray-600">
              {copied === 'ok' ? m.gaps_proposal_copied() : copied === 'failed' ? m.gaps_proposal_copy_failed() : ''}
            </span>
          </div>

          {proposal.open_facts.length > 0 && (
            <div>
              <h3 className="mb-1 text-sm font-medium text-gray-900">{m.gaps_proposal_open_facts_heading()}</h3>
              <ul className="list-disc space-y-0.5 pl-5 text-sm text-gray-900">
                {proposal.open_facts.map((fact) => (
                  <li key={fact}>{fact}</li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-xs text-gray-500">
            {proposal.language
              ? m.gaps_proposal_meta({ time: formatRelativeTime(proposal.generated_at), language: proposal.language })
              : m.gaps_proposal_meta_no_language({ time: formatRelativeTime(proposal.generated_at) })}
          </p>
        </div>
      )}

      {decided ? (
        <DecisionLine decision={decided} />
      ) : !cluster.decidable ? (
        <p className="text-sm text-gray-600">
          {m.gaps_clusters_not_decidable({ reason: notDecidableReason(clusterKey) })}
        </p>
      ) : (
        proposalQuery.isSuccess && (
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={decideMutation.isPending} onClick={() => setPending('rejected')}>
              {m.gaps_decision_reject()}
            </Button>
            <Button variant="outline" size="sm" disabled={decideMutation.isPending} onClick={() => setPending('out_of_scope')}>
              {m.gaps_decision_out_of_scope()}
            </Button>
          </div>
        )
      )}

      <AlertDialog open={pending !== null} onOpenChange={(open) => { if (!open) setPending(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pending === 'out_of_scope'
                ? m.gaps_decision_confirm_title_out_of_scope()
                : m.gaps_decision_confirm_title_rejected()}
            </AlertDialogTitle>
            <AlertDialogDescription>{m.gaps_decision_confirm_body()}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{m.gaps_close_cancel()}</AlertDialogCancel>
            <AlertDialogAction onClick={() => { if (pending) decideMutation.mutate(pending) }}>
              {m.gaps_decision_confirm()}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  )
}
