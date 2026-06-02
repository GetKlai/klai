/**
 * Characterization tests for `ProposalCard`, extracted by
 * SPEC-PORTAL-TAXONOMY-SPLIT-001 commit 4.
 *
 * Focus on the state-machine paths the extraction is most likely to
 * break (singleton edit-mode + direct reject + the edit buffer
 * initialisation on isEditing transition). Static JSX (badge
 * colours, date formatting) is intentionally NOT asserted - the live
 * Playwright pass on Voys covers that.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ProposalCard } from '../ProposalCard'
import type { TaxonomyProposal } from '../../-kb-types'

function proposal(overrides: Partial<TaxonomyProposal> = {}): TaxonomyProposal {
  return {
    id: 1,
    kb_id: 100,
    proposal_type: 'new_node',
    status: 'pending',
    title: 'New category',
    payload: { description: 'Initial description' },
    confidence_score: 0.85,
    created_at: '2026-05-13T08:00:00Z',
    reviewed_at: null,
    reviewed_by: null,
    rejection_reason: null,
    ...overrides,
  }
}

function defaultProps() {
  return {
    canEdit: true,
    isEditing: false,
    approvePending: false,
    rejectPending: false,
    onStartEdit: vi.fn(),
    onSubmitEdit: vi.fn(),
    onCancelEdit: vi.fn(),
    onReject: vi.fn(),
    onApprove: vi.fn(),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// Status branches
// ---------------------------------------------------------------------------

describe('ProposalCard - status branches', () => {
  it('renders pending status with action cluster', () => {
    render(
      <ProposalCard
        proposal={proposal({ status: 'pending', title: 'Pending one' })}
        {...defaultProps()}
      />,
    )
    expect(screen.getByText('Pending one')).toBeDefined()
    // Approve / Edit / Reject buttons all visible for pending
    expect(screen.getByText('Approve')).toBeDefined()
    expect(screen.getByText('Edit')).toBeDefined()
    expect(screen.getByText('Reject')).toBeDefined()
  })

  it('renders approved status without action cluster', () => {
    render(
      <ProposalCard
        proposal={proposal({ status: 'approved', title: 'Approved one' })}
        {...defaultProps()}
      />,
    )
    expect(screen.getByText('Approved one')).toBeDefined()
    // No Approve/Edit/Reject - only the status badge remains
    expect(screen.queryByText('Approve')).toBeNull()
    expect(screen.queryByText('Edit')).toBeNull()
    expect(screen.queryByText('Reject')).toBeNull()
  })

  it('renders rejected status with rejection_reason', () => {
    render(
      <ProposalCard
        proposal={proposal({
          status: 'rejected',
          title: 'Rejected one',
          rejection_reason: 'duplicate of #42',
        })}
        {...defaultProps()}
      />,
    )
    expect(screen.getByText('Rejected one')).toBeDefined()
    expect(screen.getByText('- duplicate of #42')).toBeDefined()
    // No action cluster
    expect(screen.queryByText('Approve')).toBeNull()
  })

  it('hides action cluster when canEdit is false even for pending', () => {
    render(
      <ProposalCard
        proposal={proposal({ status: 'pending' })}
        {...defaultProps()}
        canEdit={false}
      />,
    )
    expect(screen.queryByText('Approve')).toBeNull()
    expect(screen.queryByText('Edit')).toBeNull()
    expect(screen.queryByText('Reject')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Direct approve
// ---------------------------------------------------------------------------

describe('ProposalCard - direct approve', () => {
  it('calls onApprove when Approve button is clicked', () => {
    const props = defaultProps()
    render(<ProposalCard proposal={proposal()} {...props} />)
    fireEvent.click(screen.getByText('Approve'))
    expect(props.onApprove).toHaveBeenCalledTimes(1)
  })
})

// ---------------------------------------------------------------------------
// Edit-mode
// ---------------------------------------------------------------------------

describe('ProposalCard - edit-mode', () => {
  it('calls onStartEdit when Edit button clicked', () => {
    const props = defaultProps()
    render(<ProposalCard proposal={proposal()} {...props} />)
    fireEvent.click(screen.getByText('Edit'))
    expect(props.onStartEdit).toHaveBeenCalledTimes(1)
  })

  it('initialises edit buffers from proposal when isEditing becomes true', () => {
    const props = defaultProps()
    const p = proposal({
      title: 'Original title',
      payload: { description: 'Original description' },
    })
    const { rerender } = render(
      <ProposalCard proposal={p} {...props} isEditing={false} />,
    )
    // Flip to editing
    rerender(<ProposalCard proposal={p} {...props} isEditing />)
    expect(screen.getByDisplayValue('Original title')).toBeDefined()
    expect(screen.getByDisplayValue('Original description')).toBeDefined()
  })

  it('preserves typed buffer when proposal prop re-arrives mid-edit (regression: bug fixed in v0.2.1)', () => {
    // Simulates a TanStack Query refetch landing while the user is in
    // edit-mode: new `proposal` object reference with identical content.
    // Pre-fix the useEffect re-fired on the object-ref change and
    // overwrote the user's typed input. Post-fix the prevIsEditing ref
    // suppresses re-init unless the card transitions into edit-mode.
    const props = defaultProps()
    const p1 = proposal({
      title: 'Original',
      payload: { description: 'Initial' },
    })
    const { rerender } = render(
      <ProposalCard proposal={p1} {...props} isEditing />,
    )

    // User types
    const titleInput = screen.getByDisplayValue('Original')
    fireEvent.change(titleInput, { target: { value: 'User typed' } })
    expect((titleInput as HTMLInputElement).value).toBe('User typed')

    // New `proposal` object with same content (simulates query refetch)
    const p2 = { ...p1, payload: { ...p1.payload } }
    rerender(<ProposalCard proposal={p2} {...props} isEditing />)

    // Buffer NOT overwritten
    expect((titleInput as HTMLInputElement).value).toBe('User typed')
  })

  it('calls onSubmitEdit with trimmed title and current description', () => {
    const props = defaultProps()
    const p = proposal({
      title: 'Original',
      payload: { description: 'Initial' },
    })
    render(<ProposalCard proposal={p} {...props} isEditing />)

    const titleInput = screen.getByDisplayValue('Original')
    fireEvent.change(titleInput, { target: { value: '  Renamed  ' } })
    const descInput = screen.getByDisplayValue('Initial')
    fireEvent.change(descInput, { target: { value: 'Updated' } })

    fireEvent.click(screen.getByText('Save & Approve'))

    expect(props.onSubmitEdit).toHaveBeenCalledTimes(1)
    expect(props.onSubmitEdit).toHaveBeenCalledWith('Renamed', 'Updated')
  })

  it('calls onCancelEdit on Cancel click', () => {
    const props = defaultProps()
    render(
      <ProposalCard proposal={proposal()} {...props} isEditing />,
    )
    fireEvent.click(screen.getByText('Cancel'))
    expect(props.onCancelEdit).toHaveBeenCalledTimes(1)
    expect(props.onSubmitEdit).not.toHaveBeenCalled()
  })
})

// ---------------------------------------------------------------------------
// Direct reject
// ---------------------------------------------------------------------------

describe('ProposalCard - direct reject', () => {
  it('calls onReject when Reject button clicked', () => {
    const props = defaultProps()
    render(<ProposalCard proposal={proposal()} {...props} />)
    fireEvent.click(screen.getByText('Reject'))
    expect(props.onReject).toHaveBeenCalledTimes(1)
  })
})
