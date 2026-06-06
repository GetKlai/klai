import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Select } from '../select'

describe('Select', () => {
  it('applies sizing classes to the wrapper through containerClassName', () => {
    render(
      <Select id="setting" defaultValue="a" containerClassName="max-w-xs">
        <option value="a">A</option>
      </Select>,
    )

    const select = screen.getByRole('combobox')
    expect(select.className).not.toContain('max-w-xs')
    expect(select.parentElement?.className).toContain('max-w-xs')
  })
})
