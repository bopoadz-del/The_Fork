/* RightPanel — 300px sidebar.
 *
 * Sections:
 *   • Sources cited — live list from the latest assistant message's
 *     `sources` array (populated by the SSE `end` event in
 *     ProjectWorkspace). Empty state shows a friendly hint.
 *   • Document graph — DocumentGraph placeholder card listing the
 *     project's documents and highlighting source-cited ones.
 *
 * The panel itself is a thin layout wrapper; each section is its own
 * component so they can be moved or stubbed independently.
 */
import { type ReactNode } from 'react'
import './RightPanel.css'

interface Props {
  sources: ReactNode
  graph: ReactNode
}

export default function RightPanel({ sources, graph }: Props) {
  return (
    <div className="right-panel">
      <div className="right-panel__section">{sources}</div>
      <div className="right-panel__section">{graph}</div>
    </div>
  )
}
