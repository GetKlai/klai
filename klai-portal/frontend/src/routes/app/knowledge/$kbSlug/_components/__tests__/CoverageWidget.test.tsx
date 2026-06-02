/**
 * Characterization tests for `CoverageWidget`, extracted by
 * SPEC-PORTAL-TAXONOMY-SPLIT-001 commit 3.
 *
 * Focus on the state-machine paths the extraction is most likely to
 * break (singleton edit-mode + singleton delete-confirm + the Suggest
 * gating). Static JSX (bars, percentages, badges) is intentionally
 * NOT asserted - the live Playwright pass on Voys covers that.
 *
 * Paraglide messages are loaded for real (source language = 'en' per
 * project.inlang/settings.json); tests query against the visible
 * English strings.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CoverageWidget } from '../CoverageWidget'
import type { TaxonomyCoverage, TaxonomyCoverageNode } from '../../-kb-types'

function node(overrides: Partial<TaxonomyCoverageNode> = {}): TaxonomyCoverageNode {
  return {
    taxonomy_node_id: 1,
    taxonomy_node_name: 'Sales',
    description: 'Customers and pipeline',
    chunk_count: 10,
    gap_count: 0,
    health: 'healthy',
    ...overrides,
  }
}

function coverage(overrides: Partial<TaxonomyCoverage> = {}): TaxonomyCoverage {
  return {
    nodes: [node()],
    total_chunks: 20,
    untagged_count: 10,
    untagged_percentage: 50,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// Edit-mode singleton
// ---------------------------------------------------------------------------

describe('CoverageWidget - edit-mode', () => {
  it('starts edit on click; cancel restores original (no onRename call)', () => {
    const onRename = vi.fn()
    render(
      <CoverageWidget
        coverage={coverage({
          nodes: [node({ taxonomy_node_id: 1, taxonomy_node_name: 'Original' })],
        })}
        activeNodeId={null}
        onNodeClick={() => {}}
        canEdit
        onRename={onRename}
        onDelete={() => {}}
      />,
    )

    // Click pencil - edit mode opens, input pre-filled with current name
    fireEvent.click(screen.getByLabelText('Rename'))
    const input = screen.getByDisplayValue('Original')
    expect(input).toBeDefined()

    // Type a new name then cancel
    fireEvent.change(input, { target: { value: 'Renamed' } })
    fireEvent.click(screen.getByText('Cancel'))

    expect(onRename).not.toHaveBeenCalled()
    // Display name reverts to original
    expect(screen.getByText('Original')).toBeDefined()
  })

  it('submits edit with trimmed values via onRename', () => {
    const onRename = vi.fn()
    render(
      <CoverageWidget
        coverage={coverage({
          nodes: [node({ taxonomy_node_id: 7, taxonomy_node_name: 'Original' })],
        })}
        activeNodeId={null}
        onNodeClick={() => {}}
        canEdit
        onRename={onRename}
        onDelete={() => {}}
      />,
    )

    fireEvent.click(screen.getByLabelText('Rename'))
    const input = screen.getByDisplayValue('Original')
    fireEvent.change(input, { target: { value: '  Renamed  ' } })
    fireEvent.click(screen.getByText('Save'))

    expect(onRename).toHaveBeenCalledTimes(1)
    expect(onRename).toHaveBeenCalledWith(7, 'Renamed', 'Customers and pipeline')
  })

  it('opening edit on node B closes node A (singleton)', () => {
    render(
      <CoverageWidget
        coverage={coverage({
          nodes: [
            node({ taxonomy_node_id: 1, taxonomy_node_name: 'Sales' }),
            node({ taxonomy_node_id: 2, taxonomy_node_name: 'Marketing' }),
          ],
        })}
        activeNodeId={null}
        onNodeClick={() => {}}
        canEdit
        onRename={() => {}}
        onDelete={() => {}}
      />,
    )

    // Open A
    const renameButtons = screen.getAllByLabelText('Rename')
    fireEvent.click(renameButtons[0])
    expect(screen.getByDisplayValue('Sales')).toBeDefined()

    // Open B - pencil A is now gone (in edit mode), pencil B is still there.
    const remainingRename = screen.getAllByLabelText('Rename')
    expect(remainingRename).toHaveLength(1)
    fireEvent.click(remainingRename[0])

    // Now A's input is gone, B's input is shown
    expect(screen.queryByDisplayValue('Sales')).toBeNull()
    expect(screen.getByDisplayValue('Marketing')).toBeDefined()
  })
})

// ---------------------------------------------------------------------------
// Delete-confirm singleton
// ---------------------------------------------------------------------------

describe('CoverageWidget - delete-confirm', () => {
  it('shows confirm controls on first click; confirms delete on second', () => {
    const onDelete = vi.fn()
    render(
      <CoverageWidget
        coverage={coverage({
          nodes: [node({ taxonomy_node_id: 42 })],
        })}
        activeNodeId={null}
        onNodeClick={() => {}}
        canEdit
        onRename={() => {}}
        onDelete={onDelete}
      />,
    )

    // Click trash icon (aria-label='Delete') - confirm mode opens.
    fireEvent.click(screen.getByLabelText('Delete'))
    // Confirm Button has text 'Delete'; aria-label is gone now that the
    // icon-button is hidden in confirm mode. getByText finds only the
    // visible Button.
    fireEvent.click(screen.getByText('Delete'))

    expect(onDelete).toHaveBeenCalledTimes(1)
    expect(onDelete).toHaveBeenCalledWith(42)
  })

  it('does NOT call onDelete when user cancels', () => {
    const onDelete = vi.fn()
    render(
      <CoverageWidget
        coverage={coverage()}
        activeNodeId={null}
        onNodeClick={() => {}}
        canEdit
        onRename={() => {}}
        onDelete={onDelete}
      />,
    )

    fireEvent.click(screen.getByLabelText('Delete'))
    fireEvent.click(screen.getByText('Cancel'))

    expect(onDelete).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Suggest button gating
// ---------------------------------------------------------------------------

describe('CoverageWidget - taxonomy action CTA gating', () => {
  it('shown in empty state when total_chunks >= 10 AND onSuggest is provided', () => {
    const onSuggest = vi.fn()
    render(
      <CoverageWidget
        coverage={coverage({ nodes: [], total_chunks: 10, untagged_count: 10 })}
        activeNodeId={null}
        onNodeClick={() => {}}
        onSuggest={onSuggest}
      />,
    )
    expect(
      screen.getByText('Suggest categories'),
    ).toBeDefined()
  })

  it('hidden in empty state when total_chunks < 10', () => {
    const onSuggest = vi.fn()
    render(
      <CoverageWidget
        coverage={coverage({ nodes: [], total_chunks: 5, untagged_count: 5 })}
        activeNodeId={null}
        onNodeClick={() => {}}
        onSuggest={onSuggest}
      />,
    )
    expect(
      screen.queryByText('Suggest categories'),
    ).toBeNull()
  })

  it('shows missing-chunks CTA in populated state even when node count reaches 9', () => {
    const nodes = Array.from({ length: 9 }, (_, i) =>
      node({ taxonomy_node_id: i + 1, taxonomy_node_name: `N${i}`, chunk_count: 5 }),
    )
    const onSuggest = vi.fn()
    render(
      <CoverageWidget
        coverage={{
          nodes,
          total_chunks: 200,
          untagged_count: 100,
          untagged_percentage: 50,
        }}
        activeNodeId={null}
        onNodeClick={() => {}}
        onSuggest={onSuggest}
      />,
    )
    fireEvent.click(screen.getByText('Categorize missing chunks'))
    expect(onSuggest).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Suggest categories')).toBeNull()
  })

  it('shows missing-chunks CTA in populated state when untagged_count >= 10 AND untagged_pct > 5', () => {
    const onSuggest = vi.fn()
    render(
      <CoverageWidget
        coverage={{
          nodes: [node({ chunk_count: 100 })],
          total_chunks: 200,
          untagged_count: 100, // 50%
          untagged_percentage: 50,
        }}
        activeNodeId={null}
        onNodeClick={() => {}}
        onSuggest={onSuggest}
      />,
    )
    fireEvent.click(screen.getByText('Categorize missing chunks'))
    expect(onSuggest).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Suggest categories')).toBeNull()
  })

  it('hidden in populated state when untagged_pct ≤ 5', () => {
    const onSuggest = vi.fn()
    render(
      <CoverageWidget
        coverage={{
          nodes: [node({ chunk_count: 195 })],
          total_chunks: 200,
          untagged_count: 10, // 5% exactly - gate says > 5
          untagged_percentage: 5,
        }}
        activeNodeId={null}
        onNodeClick={() => {}}
        onSuggest={onSuggest}
      />,
    )
    expect(
      screen.queryByText('Suggest categories'),
    ).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// canEdit gating
// ---------------------------------------------------------------------------

describe('CoverageWidget - canEdit', () => {
  it('hides rename + delete icons when canEdit is false', () => {
    render(
      <CoverageWidget
        coverage={coverage()}
        activeNodeId={null}
        onNodeClick={() => {}}
        canEdit={false}
      />,
    )
    expect(screen.queryByLabelText('Rename')).toBeNull()
    expect(screen.queryByLabelText('Delete')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Node-click
// ---------------------------------------------------------------------------

describe('CoverageWidget - node click', () => {
  it('calls onNodeClick with the node id when clicking the row', () => {
    const onNodeClick = vi.fn()
    render(
      <CoverageWidget
        coverage={coverage({ nodes: [node({ taxonomy_node_id: 5, taxonomy_node_name: 'Engineering' })] })}
        activeNodeId={null}
        onNodeClick={onNodeClick}
      />,
    )
    // The row itself is role="button" with the node name inside.
    const row = screen.getByText('Engineering').closest('[role="button"]')!
    fireEvent.click(within(row as HTMLElement).getByText('Engineering'))
    expect(onNodeClick).toHaveBeenCalledWith(5)
  })
})
