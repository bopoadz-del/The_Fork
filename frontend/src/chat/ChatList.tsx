/* ChatList — scrollable message thread.
 *
 * Owns the auto-scroll-to-bottom behaviour and the empty state with
 * suggestion chips. Bubbles render via ChatBubble. The download handler
 * walks assistant messages in order so the server-side per-index lookup
 * works the same as before the redesign.
 */
import { useEffect, useRef } from 'react'
import ChatBubble from './ChatBubble'
import type { ChatMessage } from './types'
import './ChatList.css'

interface Props {
  messages: ChatMessage[]
  documentCount: number
  onSuggestion: (text: string) => void
  suggestionsDisabled: boolean
  onDownloadMessage?: (assistantIndex: number) => void
}

const EMPTY_SUGGESTIONS = [
  'What is the IT load specification?',
  'Summarise the key BOQ items',
  'What are the main project risks?',
]

export default function ChatList({
  messages,
  documentCount,
  onSuggestion,
  suggestionsDisabled,
  onDownloadMessage,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  if (messages.length === 0) {
    const docLabel =
      documentCount === 0
        ? 'No documents indexed yet for this project'
        : documentCount === 1
          ? 'I have access to 1 document in this project'
          : `I have access to ${documentCount} documents in this project`
    return (
      <div className="chat-list__empty">
        <p className="chat-list__empty-title">Ask anything about your project</p>
        <p className="chat-list__empty-hint">{docLabel}</p>
        <div className="chat-list__empty-chips" role="group" aria-label="Suggested questions">
          {EMPTY_SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              className="chat-list__chip"
              disabled={suggestionsDisabled}
              onClick={() => onSuggestion(s)}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    )
  }

  let assistantSeen = 0
  return (
    <div className="chat-list" role="log" aria-live="polite" aria-label="Conversation">
      {messages.map((msg) => {
        let downloadHandler: (() => void) | undefined
        if (msg.role === 'assistant' && !msg.streaming && !msg.error && msg.content) {
          const idx = assistantSeen
          assistantSeen += 1
          if (onDownloadMessage) {
            downloadHandler = () => onDownloadMessage(idx)
          }
        } else if (msg.role === 'assistant') {
          assistantSeen += 1
        }
        return <ChatBubble key={msg.id} message={msg} onDownload={downloadHandler} />
      })}
      <div ref={bottomRef} />
    </div>
  )
}
