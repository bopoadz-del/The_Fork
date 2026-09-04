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
 * Each row shows: confidence chip + filename + chunk reference + the
 * #468 source class (this contract / master corpus / knowledge base /
 * template). Clicking a row opens that document in the preview pane.
 */
import { FileText } from 'lucide-react'
import { sourceClassLabel } from './sourceClassLabels'
import './SourcesList.css'

export interface CitedSource {
  doc_id: string
  doc_name: string
  page_or_section: string
  score: number
  confidence: 'High' | 'Medium' | 'Low'
  project_id?: string
  layer?: string
  layer_label?: string
  source_class?: string
  source_class_label?: string
}

interface Props {
  /** Latest assistant message's sources, or undefined while streaming. */
  sources?: CitedSource[]
  /** True while the assistant message is still streaming. */
  streaming?: boolean
  /** Highlight the source currently open in the preview pane. */
  activeDocId?: string | null
  /** Open this cited document in the right-panel preview. */
  onOpenSource?: (source: CitedSource) => void
}

export default function SourcesList({ sources, streaming, activeDocId, onOpenSource }: Props) {
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
          {sources.map((s, i) => {
            const canOpen = Boolean(s.doc_id && onOpenSource)
            const isActive = Boolean(s.doc_id && s.doc_id === activeDocId)
            return (
              <li key={`${s.doc_id}-${i}`} className="sources-list__item">
                {canOpen ? (
                  <button
                    type="button"
                    className={
                      'sources-list__open' +
                      (isActive ? ' sources-list__open--active' : '')
                    }
                    onClick={() => onOpenSource?.(s)}
                    aria-pressed={isActive}
                    aria-label={`Preview ${s.doc_name || s.doc_id}`}
                  >
                    <SourceRow source={s} />
                  </button>
                ) : (
                  <div className="sources-list__static">
                    <SourceRow source={s} />
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function SourceRow({ source: s }: { source: CitedSource }) {
  const sourceClass = (s.source_class || '').trim()
  const classLabel = s.source_class_label || sourceClassLabel(sourceClass)
  return (
    <>
      <span className={`sources-list__chip sources-list__chip--${s.confidence.toLowerCase()}`}>
        {s.confidence}
      </span>
      <div className="sources-list__body">
        <div className="sources-list__doc" title={s.doc_name}>
          {s.doc_name || s.doc_id}
        </div>
        <div className="sources-list__ref">
          {s.page_or_section}
          {s.layer_label ? ` · ${s.layer_label}` : ''}
        </div>
        {sourceClass ? (
          <div
            className="sources-list__class"
            data-source-class={sourceClass}
            data-testid="source-class"
          >
            <span className="sources-list__class-label">{classLabel}</span>
            <span className="sources-list__class-code">{`class=${sourceClass}`}</span>
          </div>
        ) : null}
      </div>
    </>
  )
}
