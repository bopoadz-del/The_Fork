/* RightPanel — Quarry design 2026-06-21.
 *
 * Adds a header with title + an expand (↗) toggle. When toggled, the
 * parent shell (WorkspaceShell) flips the data attribute that promotes
 * the right panel to a full-width overlay covering main + left. The
 * caller owns the expanded state so other surfaces (header, hotkeys)
 * can drive it too.
 *
 * Content slots (sources, graph) are unchanged from PR #90 — the
 * Quarry design's BOQ-detail tabs (Description / Unit / Rate / Sheet /
 * Schedule / Output / Chart) require data wiring that's deferred to
 * the next PR (project scoping + BOQ row inspector).
 */
import { type ReactNode } from 'react'
import { ArrowUpRight, X } from 'lucide-react'
import './RightPanel.css'

interface Props {
  sources: ReactNode
  graph: ReactNode
  /** Optional title rendered in the panel header. Defaults to "Details". */
  title?: string
  /** Whether the panel is currently in expanded-overlay mode. */
  expanded?: boolean
  /** Toggle expanded mode. When undefined, the expand button is hidden. */
  onToggleExpand?: () => void
}

export default function RightPanel({
  sources, graph, title = 'Details', expanded = false, onToggleExpand,
}: Props) {
  return (
    <div className="right-panel">
      <div className="right-panel__header">
        <span className="right-panel__title">{title}</span>
        {onToggleExpand && (
          <button
            type="button"
            className="right-panel__expand"
            onClick={onToggleExpand}
            aria-label={expanded ? 'Collapse panel' : 'Expand panel to full width'}
            title={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? <X size={16} /> : <ArrowUpRight size={16} />}
          </button>
        )}
      </div>
      <div className="right-panel__body">
        <div className="right-panel__section">{sources}</div>
        <div className="right-panel__section">{graph}</div>
      </div>
    </div>
  )
}
