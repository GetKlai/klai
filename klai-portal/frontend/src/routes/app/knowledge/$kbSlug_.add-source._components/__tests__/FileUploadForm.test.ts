/* @vitest-environment jsdom */
import { describe, expect, it } from 'vitest'

import { partitionClientSide } from '../FileUploadForm'

function pdf(name: string): File {
  return new File(['%PDF-1.4\n%%EOF\n'], name, { type: 'application/pdf' })
}

describe('partitionClientSide', () => {
  it('rejects files beyond the backend max of 10 per upload', () => {
    const files = Array.from({ length: 13 }, (_, i) => pdf(`doc-${String(i + 1)}.pdf`))

    const result = partitionClientSide(files)

    expect(result.ok).toHaveLength(10)
    expect(result.rejected).toHaveLength(3)
    expect(result.rejected.map((r) => r.reason)).toEqual([
      'too_many_files',
      'too_many_files',
      'too_many_files',
    ])
  })

  it('counts already selected files when accepting another batch', () => {
    const files = Array.from({ length: 3 }, (_, i) => pdf(`extra-${String(i + 1)}.pdf`))

    const result = partitionClientSide(files, 8)

    expect(result.ok.map((f) => f.name)).toEqual(['extra-1.pdf', 'extra-2.pdf'])
    expect(result.rejected).toEqual([
      { filename: 'extra-3.pdf', reason: 'too_many_files', size: files[2].size },
    ])
  })
})
