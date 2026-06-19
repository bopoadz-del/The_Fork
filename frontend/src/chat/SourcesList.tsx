/* SourcesList — RAG citations from the latest assistant message.
 *
 * Lives in the RightPanel. Data shape comes verbatim from the SSE `end`
 * event populated by ProjectWorkspace; no fetching here.
 *
 * Empty states:
 *   • streaming  — soft "Sources will appear here once the answer
 *                  completes." message
 *   • no sources — short hint
 *
 * Each row shows: confidence chip + filename + chunk reference.
 */
import { FileText } from 'lucide-react'
import './SourcesList.css'

export interface CitedSource {
  doc_id: string
  doc_name: string
  page_or_section: string
  score: number
  confidence: 'High' | 'Medium' | 'Low'
}

interface Props {
  /** Latest assistant message's sources, or undefined while streaming. */
  sources?: CitedSource[]
  /** True while the assistant message is still streaming. */
  streaming?: boolean
}

export default function SourcesList({ sources, streaming }: Props) {
  return (
    <div className="sources-list">
      <header className="sources-list__head">
        <FileText size={14} />
        <span>Sources cited</span>
        {sources && sources.length > 0 && (
          <span className="sources-list__count">{sources.length}</span>
        )}
      </header>

      {streaming && !sources?.length ? (
        <p className="sources-list__empty">Sources appear once the answer completes.</p>
      ) : !sources?.length ? (
        <p className="sources-list__empty">No citations for the current answer.</p>
      ) : (
        <ul className="sources-list__items">
          {sources.map((s, i) => (
            <li key={`${s.doc_id}-${i}`} className="sources-list__item">
              <span className={`sources-list__chip sources-list__chip--${s.confidence.toLowerCase()}`}>
                {s.confidence}
              </span>
              <div className="sources-list__body">
                <div className="sources-list__doc" title={s.doc_name}>
                  {s.doc_name || s.doc_id}
                </div>
                <div className="sources-list__ref">{s.page_or_section}</div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
