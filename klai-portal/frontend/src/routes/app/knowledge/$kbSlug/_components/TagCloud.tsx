/**
 * Tag cloud - clickable pill-buttons sized by tag frequency.
 *
 * Pure renderer: no state, no side-effects. Active tags are styled
 * with a filled dark pill; inactive tags get a subtle outline. Font
 * size scales linearly from 0.75rem to 1rem based on the tag's count
 * relative to the most-frequent tag.
 *
 * Extracted verbatim from TaxonomyTab.tsx by SPEC-PORTAL-TAXONOMY-SPLIT-001
 * commit 2.
 */
export function TagCloud({
  tags,
  activeTags,
  onTagClick,
}: {
  tags: { tag: string; count: number }[]
  activeTags: Set<string>
  onTagClick: (tag: string) => void
}) {
  const maxCount = tags[0]?.count ?? 1

  return (
    <div className="flex flex-wrap gap-1.5">
      {tags.map(({ tag, count }) => {
        const isActive = activeTags.has(tag)
        // Scale font size from 0.75rem (min count) to 1rem (max count)
        const scale = maxCount > 1 ? (count - 1) / (maxCount - 1) : 0
        const fontSize = 0.75 + scale * 0.25

        return (
          <button
            key={tag}
            type="button"
            onClick={() => onTagClick(tag)}
            className={[
              'inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 transition-colors',
              isActive
                ? 'border-gray-900 bg-gray-900 text-white'
                : 'border-gray-200 bg-gray-50 text-gray-900 hover:bg-gray-100',
            ].join(' ')}
            style={{ fontSize: `${fontSize}rem` }}
          >
            <span>{tag}</span>
            <span className="text-xs opacity-60 tabular-nums">{count}</span>
          </button>
        )
      })}
    </div>
  )
}
