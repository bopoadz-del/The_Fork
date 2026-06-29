/* DocumentGraph — stub placeholder for the right-panel document viz.
 *
 * Operator brief explicitly approved: NO graph library this round.
 * Renders the current project's documents as a vertical list of cards.
 * Documents cited by the latest assistant answer get an accent border so
 * the "show me what was cited" affordance still works without
 * react-flow. A real graph (edges, layout) lands post-pilot.
 *
 * The list is purely a visualisation; document delete / upload affordances
 * stay in the existing DocumentsPanel (in LeftPanel). This component is
 * read-only.
 */
import { FileText, Network } from 'lucide-react'
import './DocumentGraph.css'

interface DocSummary {
  id: string
  original_name: string
  doc_type?: string
}

interface Props {
  documents: DocSummary[]
  /** doc_ids of sources cited by the latest assistant answer. */
  citedDocIds?: string[]
}

// Cap rendered nodes: the master corpus backs thousands of docs and rendering
// one SVG-bearing <li> per doc in a single commit froze the workspace on open.
const MAX_GRAPH_NODES = 200

export default function DocumentGraph({ documents, citedDocIds = [] }: Props) {
  const cited = new Set(citedDocIds)
  const visible = documents.slice(0, MAX_GRAPH_NODES)
  return (
    <div className="doc-graph">
      <header className="doc-graph__head">
        <Network size={14} />
        <span>Document graph</span>
        <span className="doc-graph__count">{documents.length}</span>
      </header>

      {documents.length === 0 ? (
        <p className="doc-graph__empty">No documents in this project yet.</p>
      ) : (
        <ul className="doc-graph__items">
          {visible.map((d) => {
            const isCited = cited.has(d.id)
            return (
              <li
                key={d.id}
                className={`doc-graph__node${isCited ? ' doc-graph__node--cited' : ''}`}
                title={isCited ? 'Cited in the current answer' : d.original_name}
              >
                <FileText size={14} className="doc-graph__icon" />
                <span className="doc-graph__name">{d.original_name}</span>
                {isCited && <span className="doc-graph__cited">cited</span>}
              </li>
            )
          })}
        </ul>
      )}

      <p className="doc-graph__hint">
        Edges and relationships ship after pilot. Cited documents highlight when an
        answer completes.
      </p>
    </div>
  )
}
