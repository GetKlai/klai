// SPEC-KNOWLEDGE-ACTIVITY-001 §4.6/§4.7: the calibration readout above the
// conversation filters — how certain the system was, whether that certainty
// was justified, and how often the nightly judge and the human reviewer
// agree. The three sentences carry the numbers that matter; the band/judge/
// language breakdown is secondary, so it sits behind a closed Chat
// Disclosure Row (docs/ui-standards.md).
import { ChevronRight } from 'lucide-react'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import * as m from '@/paraglide/messages'
import type { ActivitySummary, ConversationBand } from './api'

const BAND_LABEL: Record<ConversationBand, () => string> = {
  high: m.activity_band_high,
  medium: m.activity_band_medium,
  low: m.activity_band_low,
  unknown: m.activity_band_unknown,
}

const OUTCOME_LABEL: Record<string, () => string> = {
  resolved: m.activity_outcome_resolved,
  partially_resolved: m.activity_outcome_partially_resolved,
  escalated: m.activity_outcome_escalated,
  unresolved: m.activity_outcome_unresolved,
  abandoned_early: m.activity_outcome_abandoned_early,
  out_of_scope: m.activity_outcome_out_of_scope,
}

const HUMAN_CAUSE_LABEL: Record<string, () => string> = {
  knowledge_missing: m.activity_cause_knowledge_missing,
  knowledge_wrong: m.activity_cause_knowledge_wrong,
  behaviour: m.activity_cause_behaviour,
  none: m.activity_cause_none,
}

function pct(numerator: number, denominator: number): number {
  return denominator > 0 ? Math.round((numerator / denominator) * 100) : 0
}

interface TableRow {
  label: string
  reviewed: number
  correct: number
}

function CalibrationTable({ caption, rows }: { caption: string; rows: TableRow[] }) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-gray-600">{caption}</p>
      <DataTable>
        <DataTableHeader>
          <DataTableRow>
            <DataTableHead>{m.activity_calibration_col_label()}</DataTableHead>
            <DataTableHead align="right">{m.activity_calibration_col_reviewed()}</DataTableHead>
            <DataTableHead align="right">{m.activity_calibration_col_correct()}</DataTableHead>
          </DataTableRow>
        </DataTableHeader>
        <DataTableBody>
          {rows.map((row) => (
            <DataTableRow key={row.label}>
              <DataTableCell>{row.label}</DataTableCell>
              <DataTableCell align="right" className="tabular-nums">
                {row.reviewed}
              </DataTableCell>
              <DataTableCell align="right" className="tabular-nums">
                {row.correct}
              </DataTableCell>
            </DataTableRow>
          ))}
        </DataTableBody>
      </DataTable>
    </div>
  )
}

function JudgeCategoryTable({ caption, rows }: { caption: string; rows: ActivitySummary['by_judge_category'] }) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-gray-600">{caption}</p>
      <DataTable>
        <DataTableHeader>
          <DataTableRow>
            <DataTableHead>{m.activity_calibration_col_judge_category()}</DataTableHead>
            <DataTableHead>{m.activity_calibration_col_human_cause()}</DataTableHead>
            <DataTableHead align="right">{m.activity_calibration_col_count()}</DataTableHead>
          </DataTableRow>
        </DataTableHeader>
        <DataTableBody>
          {rows.map((row) => (
            <DataTableRow key={`${row.judge_category ?? '\u2205'}|${row.human_cause}`}>
              <DataTableCell>{row.judge_category ?? m.activity_calibration_no_judgment()}</DataTableCell>
              <DataTableCell>
                {(HUMAN_CAUSE_LABEL[row.human_cause] ?? (() => row.human_cause))()}
              </DataTableCell>
              <DataTableCell align="right" className="tabular-nums">
                {row.count}
              </DataTableCell>
            </DataTableRow>
          ))}
        </DataTableBody>
      </DataTable>
    </div>
  )
}

export function CalibrationPanel({ summary }: { summary: ActivitySummary }) {
  if (summary.reviewed === 0) {
    return <p className="text-sm text-gray-600">{m.activity_calibration_empty()}</p>
  }

  const highBand = summary.by_band.find((row) => row.band === 'high')
  // Agreement: a resolved judge outcome agrees with a human "correct" verdict,
  // any other judge outcome agrees with a human verdict that was not correct.
  const judgeRows = summary.by_judge_outcome.filter((row) => row.judge_outcome !== null)
  const judgeAgreeing = judgeRows.reduce(
    (sum, row) =>
      sum + (row.judge_outcome === 'resolved' ? row.human_correct : row.reviewed - row.human_correct),
    0,
  )
  const judgeTotal = judgeRows.reduce((sum, row) => sum + row.reviewed, 0)

  return (
    <div className="rounded-xl border border-gray-200 p-4 space-y-3">
      <div className="space-y-1 text-sm text-gray-900">
        <p>
          {highBand && highBand.reviewed > 0
            ? m.activity_calibration_high_band({
                pct: String(pct(highBand.correct, highBand.reviewed)),
                n: String(highBand.reviewed),
              })
            : m.activity_calibration_high_band_empty()}
        </p>
        <p>
          {m.activity_calibration_judge_agreement({
            pct: String(pct(judgeAgreeing, judgeTotal)),
            n: String(judgeTotal),
          })}
        </p>
        <p>
          {m.activity_calibration_modes({
            openPct: String(pct(summary.broad_mode.correct, summary.broad_mode.reviewed)),
            openN: String(summary.broad_mode.reviewed),
            strictPct: String(pct(summary.strict_on_gap.correct, summary.strict_on_gap.reviewed)),
            strictN: String(summary.strict_on_gap.reviewed),
          })}
        </p>
      </div>

      <details className="group max-w-2xl bg-transparent">
        <summary className="inline-flex min-h-7 cursor-pointer list-none items-center gap-1.5 rounded-md px-1 py-0.5 text-[13px] text-[color:rgb(25_25_24_/_0.5)] hover:bg-[var(--color-muted)]/60 hover:text-gray-900 [&::-webkit-details-marker]:hidden">
          <ChevronRight className="h-3 w-3 shrink-0 text-[color:rgb(25_25_24_/_0.3)] transition-transform group-open:rotate-90" />
          <span className="min-w-0 flex-1 font-medium">{m.activity_calibration_details()}</span>
        </summary>
        <div className="space-y-4 pb-2 pl-4 pt-2">
          <CalibrationTable
            caption={m.activity_filter_band()}
            rows={summary.by_band.map((row) => ({
              label: BAND_LABEL[row.band](),
              reviewed: row.reviewed,
              correct: row.correct,
            }))}
          />
          <CalibrationTable
            caption={m.activity_filter_judge_outcome()}
            rows={summary.by_judge_outcome.map((row) => ({
              label: row.judge_outcome
                ? (OUTCOME_LABEL[row.judge_outcome] ?? (() => row.judge_outcome as string))()
                : m.activity_calibration_no_judgment(),
              reviewed: row.reviewed,
              correct: row.human_correct,
            }))}
          />
          <CalibrationTable
            caption={m.activity_filter_language()}
            rows={summary.by_language.map((row) => ({
              label: row.language ?? m.activity_calibration_unknown_language(),
              reviewed: row.reviewed,
              correct: row.correct,
            }))}
          />
          <JudgeCategoryTable
            caption={m.activity_calibration_by_judge_category()}
            rows={summary.by_judge_category}
          />
        </div>
      </details>
    </div>
  )
}
