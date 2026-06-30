/* ChatBubble — one message rendered as a chat bubble.
 *
 * Behaviour preserved from the pre-redesign ProjectWorkspace bubble:
 *   • user vs assistant role (right vs left aligned via CSS)
 *   • error variant with the alert SVG + role="alert"
 *   • streaming dots animation when content is empty
 *   • tool-status mini-bubble shown above content
 *   • markdown rendering with ReactMarkdown + remark-gfm
 *   • download button on completed assistant messages
 *
 * REMOVED: the inline `<details>` sources footer. Sources now live in
 * RightPanel/SourcesList for the LATEST answer (operator spec — moved
 * to right panel for visibility).
 */
import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { AlertTriangle, Download } from 'lucide-react'
import './ChatBubble.css'

import type { ChatMessage, ExportDescriptor } from './types'

interface Props {
  message: ChatMessage
  onDownload?: () => void
  onExport?: (descriptor: ExportDescriptor) => void
}

// Memoised: every streamed token calls setMessages on the parent, which
// re-renders the whole list. Without memo, each tick re-parses ReactMarkdown
// for every prior bubble — janky in long sessions. Bubbles are immutable once
// settled, so a shallow prop compare skips them.
function ChatBubble({ message, onDownload, onExport }: Props) {
  const isUser = message.role === 'user'
  const exports = message.exports ?? []

  if (message.error) {
    return (
      <div className="chat-bubble chat-bubble--error" role="alert">
        <AlertTriangle size={18} className="chat-bubble__error-icon" />
        <span>{message.content || 'Something went wrong. Please try again.'}</span>
      </div>
    )
  }

  return (
    <div className={`chat-bubble chat-bubble--${message.role}`}>
      {!isUser && <div className="chat-bubble__avatar" aria-hidden="true" title="The SHovel">TSH</div>}

      <div className="chat-bubble__body">
        {message.toolStatus && (
          <div className="chat-bubble__tool-status" aria-live="polite">
            {message.toolStatus}
          </div>
        )}

        <div className="chat-bubble__content">
          {isUser ? (
            <span className="chat-bubble__text">{message.content}</span>
          ) : message.streaming && !message.content ? (
            <span className="chat-bubble__typing" aria-label="Assistant is thinking">
              <span className="chat-bubble__typing-dot" />
              <span className="chat-bubble__typing-dot" />
              <span className="chat-bubble__typing-dot" />
            </span>
          ) : (
            <div className="chat-bubble__markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
              {message.streaming && <span className="chat-bubble__cursor" aria-hidden="true" />}
            </div>
          )}
        </div>

        {message.role === 'assistant' && !message.streaming && message.content && (onDownload || exports.length > 0) && (
          <div className="chat-bubble__actions">
            {onDownload && (
              <button
                type="button"
                className="chat-bubble__download"
                onClick={onDownload}
                title="Download this message as a Word document"
                aria-label="Download as Word document"
              >
                <Download size={13} />
                <span>Download</span>
              </button>
            )}
            {exports.map((exp, i) => (
              <button
                key={`${exp.endpoint}-${i}`}
                type="button"
                className="chat-bubble__download chat-bubble__download--export"
                onClick={() => onExport?.(exp)}
                title={`Generate and download: ${exp.label}`}
                aria-label={`Download ${exp.label}`}
              >
                <Download size={13} />
                <span>{exp.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {isUser && <div className="chat-bubble__avatar chat-bubble__avatar--user" aria-hidden="true">U</div>}
    </div>
  )
}

export default memo(ChatBubble)
