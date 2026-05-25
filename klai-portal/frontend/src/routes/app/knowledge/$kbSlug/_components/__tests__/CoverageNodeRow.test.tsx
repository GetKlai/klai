/**
 * Characterization tests for `CoverageNodeRow`, extracted from
 * CoverageWidget by SPEC-PORTAL-TAXONOMY-SPLIT-001 polish round.
 *
 * Focus: the state-machine paths the extraction is most likely to
 * break - singleton edit-mode + delete-confirm, buffer initialisation
 * on isEditing transition, buffer preservation across prop refetches
 * (same regression class as ProposalCard's useEffect bug fix).
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CoverageNodeRow } from '../CoverageNodeRow'
import type { TaxonomyCoverageNode } from '../../-kb-types'

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

function defaultProps() {
  return {
    totalChunks: 100,
    isActive: false,
    isEditing: false,
    isConfirmingDelete: false,
    canEdit: true,
    onNodeClick: vi.fn(),
    onStartEdit: vi.fn(),
    onSubmitEdit: vi.fn(),
    onCancelEdit: vi.fn(),
    onStartDelete: vi.fn(),
    onConfirmDelete: vi.fn(),
    onCancelDelete: vi.fn(),
  }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.restoreAllMocks()
})

// ---------------------------------------------------------------------------
// Static rendering
// ---------------------------------------------------------------------------

describe('CoverageNodeRow - static rendering', () => {
  it('renders node name, description, and percentage', () => {
    render(
      <CoverageNodeRow
        node={node({ taxonomy_node_name: 'Engineering', chunk_count: 25 })}
        {...defaultProps()}
      />,
    )
    expect(screen.getByText('Engineering')).toBeDefined()
    expect(screen.getByText('Customers and pipeline')).toBeDefined()
    expect(screen.getByText('25%')).toBeDefined()
  })

  it('hides rename + delete icons when canEdit is false', () => {
    render(
      <CoverageNodeRow
        node={node()}
        {...defaultProps()}
        canEdit={false}
      />,
    )
    expect(screen.queryByLabelText('Rename')).toBeNull()
    expect(screen.queryByLabelText('Delete')).toBeNull()
  })

  it('calls onNodeClick when the row is clicked', () => {
    const props = defaultProps()
    render(<CoverageNodeRow node={node()} {...props} />)
    fireEvent.click(screen.getByText('Sales'))
    expect(props.onNodeClick).toHaveBeenCalledTimes(1)
  })
})

// ---------------------------------------------------------------------------
// Edit-mode
// ---------------------------------------------------------------------------

describe('CoverageNodeRow - edit-mode', () => {
  it('calls onStartEdit when Rename pencil is clicked', () => {
    const props = defaultProps()
    render(<CoverageNodeRow node={node()} {...props} />)
    fireEvent.click(screen.getByLabelText('Rename'))
    expect(props.onStartEdit).toHaveBeenCalledTimes(1)
  })

  it('initialises edit buffers when isEditing flips to true', () => {
    const props = defaultProps()
    const n = node({ taxonomy_node_name: 'Original', description: 'Original desc' })
    const { rerender } = render(<CoverageNodeRow node={n} {...props} isEditing={false} />)
    rerender(<CoverageNodeRow node={n} {...props} isEditing />)
    expect(screen.getByDisplayValue('Original')).toBeDefined()
    expect(screen.getByDisplayValue('Original desc')).toBeDefined()
  })

  it('calls onSubmitEdit with trimmed name + description', () => {
    const props = defaultProps()
    render(
      <CoverageNodeRow
        node={node({ taxonomy_node_name: 'Original', description: 'Initial' })}
        {...props}
        isEditing
      />,
    )

    const nameInput = screen.getByDisplayValue('Original')
    fireEvent.change(nameInput, { target: { value: '  Renamed  ' } })
    const descInput = screen.getByDisplayValue('Initial')
    fireEvent.change(descInput, { target: { value: '  Updated  ' } })

    fireEvent.click(screen.getByText('Save'))

    expect(props.onSubmitEdit).toHaveBeenCalledTimes(1)
    expect(props.onSubmitEdit).toHaveBeenCalledWith('Renamed', 'Updated')
  })

  it('calls onCancelEdit on Cancel click; does not call onSubmitEdit', () => {
    const props = defaultProps()
    render(<CoverageNodeRow node={node()} {...props} isEditing />)
    fireEvent.click(screen.getByText('Cancel'))
    expect(props.onCancelEdit).toHaveBeenCalledTimes(1)
    expect(props.onSubmitEdit).not.toHaveBeenCalled()
  })

  it('preserves typed buffer when node prop re-arrives mid-edit (regression: query refetch)', () => {
    // Simulates a TanStack Query refetch landing while the user is in
    // edit-mode: new `node` object reference with identical content.
    // The useRef transition guard must suppress re-init of the buffer.
    const props = defaultProps()
    const n1 = node({ taxonomy_node_name: 'Original' })
    const { rerender } = render(<CoverageNodeRow node={n1} {...props} isEditing />)

    const input = screen.getByDisplayValue('Original')
    fireEvent.change(input, { target: { value: 'User typed' } })
    expect((input as HTMLInputElement).value).toBe('User typed')

    // New node object, same content
    const n2 = { ...n1 }
    rerender(<CoverageNodeRow node={n2} {...props} isEditing />)

    expect((input as HTMLInputElement).value).toBe('User typed')
  })

  it('does not submit when the name buffer is empty', () => {
    const props = defaultProps()
    render(
      <CoverageNodeRow
        node={node({ taxonomy_node_name: 'Original' })}
        {...props}
        isEditing
      />,
    )
    fireEvent.change(screen.getByDisplayValue('Original'), { target: { value: '   ' } })
    // Submit button should be disabled
    const submit = screen.getByText('Save')
    expect((submit as HTMLButtonElement).disabled).toBe(true)
  })
})

// ---------------------------------------------------------------------------
// Delete-confirm
// ---------------------------------------------------------------------------

describe('CoverageNodeRow - delete-confirm', () => {
  it('calls onStartDelete when trash icon is clicked', () => {
    const props = defaultProps()
    render(<CoverageNodeRow node={node()} {...props} />)
    fireEvent.click(screen.getByLabelText('Delete'))
    expect(props.onStartDelete).toHaveBeenCalledTimes(1)
  })

  it('shows confirm + cancel controls when isConfirmingDelete is true; trash icon hidden', () => {
    render(<CoverageNodeRow node={node()} {...defaultProps()} isConfirmingDelete />)
    // The icon-button trash is gone; only the destructive confirm Button text remains.
    expect(screen.queryByLabelText('Delete')).toBeNull()
    expect(screen.getByText('Delete')).toBeDefined()
    expect(screen.getByText('Cancel')).toBeDefined()
  })

  it('calls onConfirmDelete on confirm click', () => {
    const props = defaultProps()
    render(<CoverageNodeRow node={node()} {...props} isConfirmingDelete />)
    fireEvent.click(screen.getByText('Delete'))
    expect(props.onConfirmDelete).toHaveBeenCalledTimes(1)
  })

  it('calls onCancelDelete on Cancel click', () => {
    const props = defaultProps()
    render(<CoverageNodeRow node={node()} {...props} isConfirmingDelete />)
    fireEvent.click(screen.getByText('Cancel'))
    expect(props.onCancelDelete).toHaveBeenCalledTimes(1)
  })
})

// ---------------------------------------------------------------------------
// Active state
// ---------------------------------------------------------------------------

describe('CoverageNodeRow - active filter', () => {
  it('does not invoke onNodeClick when isEditing is true', () => {
    const props = defaultProps()
    render(<CoverageNodeRow node={node()} {...props} isEditing />)
    // Click anywhere on the wrapping row - should not fire because
    // the row guards on isEditing && isConfirmingDelete.
    const row = screen.getAllByRole('button')[0] // outer wrapping div has role='button'
    fireEvent.click(row)
    expect(props.onNodeClick).not.toHaveBeenCalled()
  })
})
