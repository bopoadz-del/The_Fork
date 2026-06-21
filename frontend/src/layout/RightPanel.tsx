/* RightPanel — Quarry tabs + expand-overlay only.
 *
 * Header tabs (item 18 of UI audit):
 *   Sources  — citations from the latest assistant message
 *   Doc      — DocumentGraph placeholder
 *   Sheet    — TBD (BOQ row inspector, needs row-selection wiring)
 *   Schedule — TBD
 *   Chart    — TBD
 *
 * Expand (↗): toggles full-width overlay (already wired in
 * WorkspaceShell via data-right-expanded).
 *
 * Drag / dock / float / resize were briefly shipped in PR #105 and
 * stripped per operator brief — post-pilot complexity, not needed for
 * the Dar Al Arkan pilot. Tabs + expand stay.
 */
import { useState, type ReactNode } from 'react'
import { ArrowUpRight, X } from 'lucide-react'
import './RightPanel.css'

interface Props {
  sources: ReactNode
  graph: ReactNode
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

export default function RightPanel({
  sources, graph, expanded = false, onToggleExpand,
}: Props) {
  const [tab, setTab] = useState<TabKey>('sources')

  function renderBody() {
    switch (tab) {
      case 'sources': return <div className="right-panel__section">{sources}</div>
      case 'graph':   return <div className="right-panel__section">{graph}</div>
      case 'sheet':
        return (
          <div className="right-panel__section right-panel__placeholder">
            Sheet view — BOQ rows inspector. Pick a line item in the chat
            to populate.
          </div>
        )
      case 'schedule':
        return (
          <div className="right-panel__section right-panel__placeholder">
            Schedule view — activity timeline. Generate a WBS via the chat
            to populate.
          </div>
        )
      case 'chart':
        return (
          <div className="right-panel__section right-panel__placeholder">
            Chart view — cost / progress visualizations. Wires to BOQ +
            schedule data when available.
          </div>
        )
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
