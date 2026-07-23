/* RightPanel — Quarry tabs + expand-overlay only.
 *
 * Header tabs (item 18 of UI audit):
 *   Sources  — citations from the latest assistant message
 *   Doc      — DocumentGraph (read-only document list)
 *   Sheet    — inline table preview of a selected spreadsheet / BOQ
 *   Schedule — inline preview of a selected schedule artifact
 *   Chart    — inline preview of a selected chart-bearing workbook
 *
 * Sheet / Schedule / Chart share a document picker + DocumentPreview: the
 * user chooses which stored artifact to preview and it renders inline (xlsx
 * table, pdf embed, or extracted text) rather than forcing a download.
 *
 * Expand (↗): toggles full-width overlay (already wired in
 * WorkspaceShell via data-right-expanded).
 *
 * Drag / dock / float / resize were briefly shipped in PR #105 and
 * stripped per operator brief — post-pilot complexity, not needed for
 * the Dar Al Arkan pilot. Tabs + expand stay.
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ArrowUpRight, X } from 'lucide-react'
import DocumentPreview from '../documents/DocumentPreview'
import './RightPanel.css'

export interface PreviewDocument {
  id: string
  original_name: string
  doc_type?: string
}

interface Props {
  sources: ReactNode
  graph: ReactNode
  /** Project the previewable documents belong to. */
  projectId?: string
  /** Documents available to preview (newest first — index 0 is the default). */
  documents?: PreviewDocument[]
  /** Title slot kept for backwards compat — not rendered alongside tabs. */
  title?: string
  expanded?: boolean
  onToggleExpand?: () => void
}

type TabKey = 'sources' | 'graph' | 'sheet' | 'schedule' | 'chart'

interface TabDef {
  key: TabKey
  label: string
}

const TABS: TabDef[] = [
  { key: 'sources', label: 'Sources' },
  { key: 'graph', label: 'Doc' },
  { key: 'sheet', label: 'Sheet' },
  { key: 'schedule', label: 'Schedule' },
  { key: 'chart', label: 'Chart' },
]

/** Empty-state copy per preview tab (functional wording — never "placeholder"). */
const PREVIEW_EMPTY: Record<'sheet' | 'schedule' | 'chart', string> = {
  sheet: 'No document selected. Choose a spreadsheet or BOQ to preview its rows.',
  schedule: 'No document selected. Choose a schedule to preview it.',
  chart: 'No document selected. Choose a workbook to preview its sheets.',
}

export default function RightPanel({
  sources, graph, projectId, documents = [], expanded = false, onToggleExpand,
}: Props) {
  const [tab, setTab] = useState<TabKey>('sources')
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null)

  // Default the picker to the most recently added document, and keep the
  // selection valid as the document list changes (e.g. an upload or delete).
  useEffect(() => {
    if (documents.length === 0) {
      setSelectedDocId(null)
      return
    }
    setSelectedDocId((prev) =>
      prev && documents.some((d) => d.id === prev) ? prev : documents[0].id,
    )
  }, [documents])

  const selectedDoc = useMemo(
    () => documents.find((d) => d.id === selectedDocId) ?? null,
    [documents, selectedDocId],
  )

  function renderPreviewTab(kind: 'sheet' | 'schedule' | 'chart') {
    return (
      <div className="right-panel__section">
        {documents.length === 0 ? (
          <div className="right-panel__placeholder">{PREVIEW_EMPTY[kind]}</div>
        ) : (
          <>
            <label className="right-panel__picker">
              <span className="right-panel__picker-label">Preview</span>
              <select
                className="right-panel__picker-select"
                value={selectedDocId ?? ''}
                onChange={(e) => setSelectedDocId(e.target.value)}
                aria-label="Select a document to preview"
              >
                {documents.map((d) => (
                  <option key={d.id} value={d.id}>{d.original_name}</option>
                ))}
              </select>
            </label>
            <DocumentPreview
              projectId={projectId ?? ''}
              document={selectedDoc}
              emptyLabel={PREVIEW_EMPTY[kind]}
            />
          </>
        )}
      </div>
    )
  }

  function renderBody() {
    switch (tab) {
      case 'sources':  return <div className="right-panel__section">{sources}</div>
      case 'graph':    return <div className="right-panel__section">{graph}</div>
      case 'sheet':    return renderPreviewTab('sheet')
      case 'schedule': return renderPreviewTab('schedule')
      case 'chart':    return renderPreviewTab('chart')
    }
  }

  return (
    <div className="right-panel">
      <div className="right-panel__header">
        <div className="right-panel__tabs" role="tablist">
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={tab === t.key}
              className={
                'right-panel__tab' +
                (tab === t.key ? ' right-panel__tab--active' : '')
              }
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="right-panel__actions">
          {onToggleExpand && (
            <button
              type="button"
              className="right-panel__icon-btn"
              onClick={onToggleExpand}
              aria-label={expanded ? 'Collapse panel' : 'Expand panel to full width'}
              title={expanded ? 'Collapse' : 'Expand'}
            >
              {expanded ? <X size={14} /> : <ArrowUpRight size={14} />}
            </button>
          )}
        </div>
      </div>

      <div className="right-panel__body">{renderBody()}</div>
    </div>
  )
}
