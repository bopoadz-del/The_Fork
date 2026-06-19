/* LeftPanel — 240px desktop sidebar.
 *
 * Sections:
 *   • Projects — link back to the projects list.
 *   • Documents (existing DocumentsPanel) — uploaded / Drive-imported files
 *     with delete, status badges, file-type chips. Reusing the existing
 *     component preserves PR #87 and earlier behaviour without re-styling
 *     the file rows.
 *   • Drive — the Drive picker (folder nav from PR #87) for adding new docs.
 *   • Chat history — placeholder section (post-pilot).
 *
 * Each section is a card; sections separated by 12px gap. The chat-history
 * placeholder is rendered as a subtle "Coming soon" tile so the section
 * structure is visible from day one.
 */
import { type ReactNode } from 'react'
import { FolderOpen, MessagesSquare } from 'lucide-react'
import { Link } from 'react-router-dom'
import './LeftPanel.css'

interface Props {
  /** Documents card content (existing DocumentsPanel rendered by caller). */
  documents: ReactNode
  /** Drive picker content (existing DrivePanel rendered by caller). */
  drive: ReactNode
}

export default function LeftPanel({ documents, drive }: Props) {
  return (
    <div className="left-panel">
      <section className="left-panel__section">
        <header className="left-panel__section-head">
          <FolderOpen size={14} />
          <span>Projects</span>
        </header>
        <Link to="/" className="left-panel__nav-link">
          All projects
        </Link>
      </section>

      <section className="left-panel__section">
        {documents}
      </section>

      <section className="left-panel__section">
        {drive}
      </section>

      <section className="left-panel__section">
        <header className="left-panel__section-head">
          <MessagesSquare size={14} />
          <span>Chat history</span>
        </header>
        <p className="left-panel__placeholder">Past conversations will appear here.</p>
      </section>
    </div>
  )
}
