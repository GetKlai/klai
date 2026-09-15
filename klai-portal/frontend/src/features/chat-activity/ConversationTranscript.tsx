import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { _isSafeHttpUrl } from './urlAllowlist'
import type { ConversationMessage } from './types'

/**
 * Chat bubbles for one conversation: user right, assistant left, sources and
 * the customer's thumbs mark under the assistant answer. Renders a fragment so
 * the surrounding drawer keeps owning the vertical rhythm.
 */
export function ConversationTranscript({
  messages,
}: {
  messages: ConversationMessage[]
}) {
  return (
    <>
      {messages.map((msg) => (
        <div
          key={msg.id}
          className={
            msg.role === 'user'
              ? 'ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-gray-900 px-4 py-2.5 text-sm text-white whitespace-pre-wrap'
              : 'mr-auto max-w-[85%] rounded-2xl rounded-bl-md bg-[var(--color-rl-cream)] px-4 py-2.5 text-sm text-gray-900 whitespace-pre-wrap'
          }
        >
          {msg.content}
          {msg.role === 'assistant' && msg.sources && msg.sources.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {msg.sources.map((s) => (
                <li key={`${msg.id}-${s.label}`}>
                  {/* REQ-9: only http/https schemes render as anchors */}
                  {_isSafeHttpUrl(s.url) ? (
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={s.title}
                      className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[0.6875rem] text-gray-700 klai-hover"
                    >
                      <span className="font-medium">({s.label})</span>
                      <span className="truncate max-w-[12rem]">{s.title}</span>
                    </a>
                  ) : (
                    <span
                      title={s.title}
                      className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-0.5 text-[0.6875rem] text-gray-700"
                    >
                      <span className="font-medium">({s.label})</span>
                      <span className="truncate max-w-[12rem]">{s.title}</span>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {msg.role === 'assistant' && msg.rating && (
            msg.rating === 'thumbsUp' ? (
              <ThumbsUp
                role="img"
                aria-label="Door klant beoordeeld met duim omhoog"
                className="mt-2 h-3.5 w-3.5 text-[var(--color-success-text)]"
              />
            ) : (
              <ThumbsDown
                role="img"
                aria-label="Door klant beoordeeld met duim omlaag"
                className="mt-2 h-3.5 w-3.5 text-[var(--color-destructive)]"
              />
            )
          )}
        </div>
      ))}
    </>
  )
}
