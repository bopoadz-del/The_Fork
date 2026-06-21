/* RightPanel — Quarry tabs + resize + pop-out (audit follow-up).
 *
 * Tabs (item 18 of UI audit):
 *   Sources  — citations from the latest assistant message
 *   Graph    — DocumentGraph placeholder
 *   Sheet    — TBD (BOQ row inspector — needs row-selection wiring)
 *   Schedule — TBD
 *   Chart    — TBD
 *
 * Expand (↗): toggles full-width overlay (already wired).
 *
 * Resize handle (item 20): drag the left edge to widen/narrow the panel.
 *   Width persists to localStorage as "fork.rightPanelWidth".
 *
 * Pop-out (item 20): toggles a floating draggable mode. When floating,
 *   the panel becomes position: fixed and shows a drag header.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ArrowUpRight, X, GripVertical, PictureInPicture2 } from 'lucide-react'
import './RightPanel.css'

interface Props {
  sources: ReactNode
  graph: ReactNode
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

const DEFAULT_WIDTH = 360
const MIN_WIDTH = 280
const MAX_WIDTH = 720

export default function RightPanel({
  sources, graph, expanded = false, onToggleExpand,
}: Props) {
  const [tab, setTab] = useState<TabKey>('sources')
  const [width, setWidth] = useState<number>(() => {
    if (typeof window === 'undefined') return DEFAULT_WIDTH
    const stored = Number(window.localStorage.getItem('fork.rightPanelWidth'))
    return Number.isFinite(stored) && stored >= MIN_WIDTH && stored <= MAX_WIDTH
      ? stored : DEFAULT_WIDTH
  })
  const [floating, setFloating] = useState(false)
  const [floatPos, setFloatPos] = useState({ x: 60, y: 100 })

  // Push width onto the parent so WorkspaceShell can pick it up.
  useEffect(() => {
    const el = document.querySelector('.workspace-shell__right') as HTMLElement | null
    if (el && !expanded && !floating) {
      el.style.flexBasis = `${width}px`
      window.localStorage.setItem('fork.rightPanelWidth', String(width))
    } else if (el) {
      // Reset to CSS default when overlay or floating mode takes over.
      el.style.removeProperty('flex-basis')
    }
  }, [width, expanded, floating])

  // Resize drag — anchored to the left edge of the panel.
  const resizingRef = useRef(false)
  function startResize(e: React.MouseEvent) {
    e.preventDefault()
    resizingRef.current = true
    const startX = e.clientX
    const startW = width
    function onMove(ev: MouseEvent) {
      if (!resizingRef.current) return
      // Dragging LEFT (smaller clientX) widens the right panel.
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startW + (startX - ev.clientX)))
      setWidth(next)
    }
    function onUp() {
      resizingRef.current = false
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // Floating drag — header bar drags the whole panel.
  const draggingRef = useRef<{ dx: number; dy: number } | null>(null)
  function startFloatDrag(e: React.MouseEvent) {
    if (!floating) return
    draggingRef.current = { dx: e.clientX - floatPos.x, dy: e.clientY - floatPos.y }
    function onMove(ev: MouseEvent) {
      const d = draggingRef.current
      if (!d) return
      setFloatPos({ x: Math.max(0, ev.clientX - d.dx), y: Math.max(0, ev.clientY - d.dy) })
    }
    function onUp() {
      draggingRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

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

  const containerClass =
    'right-panel' +
    (floating ? ' right-panel--floating' : '') +
    (expanded ? ' right-panel--expanded' : '')

  const containerStyle: React.CSSProperties = floating
    ? { left: floatPos.x, top: floatPos.y, width: width }
    : {}

  return (
    <div className={containerClass} style={containerStyle}>
      {/* Resize handle — only docked mode + not overlay. */}
      {!floating && !expanded && (
        <div
          className="right-panel__resize"
          onMouseDown={startResize}
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panel"
          title="Drag to resize"
        />
      )}

      <div
        className="right-panel__header"
        onMouseDown={floating ? startFloatDrag : undefined}
      >
        {floating && (
          <span className="right-panel__drag" aria-label="Drag panel">
            <GripVertical size={14} />
          </span>
        )}
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
          <button
            type="button"
            className="right-panel__icon-btn"
            onClick={() => setFloating((v) => !v)}
            aria-label={floating ? 'Dock panel' : 'Float panel'}
            title={floating ? 'Dock' : 'Float'}
          >
            <PictureInPicture2 size={14} />
          </button>
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
