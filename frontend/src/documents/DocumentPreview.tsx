/* DocumentPreview — inline preview of a stored project artifact.
 *
 * Fetches the render-friendly JSON from the backend preview endpoint and
 * renders it inline in the right panel instead of forcing a download:
 *   kind table       → scrollable HTML table (sheet tabs when multi-sheet)
 *   kind pdf          → <object> over a blob object URL (auth stays in the
 *                       Authorization header — the raw route is bearer-gated,
 *                       so we fetch the bytes and embed a blob URL rather than
 *                       pointing the element at a URL an iframe can't authorize)
 *   kind text         → formatted <pre> block
 *   kind unsupported  → functional message naming the file type
 */
import { useEffect, useState } from 'react'
import { apiGet, ApiError } from '../lib/api'
import { getToken } from '../lib/token'
import './DocumentPreview.css'

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

interface DocRef {
  id: string
  original_name: string
}

interface Props {
  projectId: string
  document: DocRef | null
  /** Functional empty-state copy shown when no document is selected. */
  emptyLabel?: string
}

type PreviewData =
  | { kind: 'table'; sheets: Array<{ name: string; rows: string[][] }>; truncated?: boolean }
  | { kind: 'pdf' }
  | { kind: 'text'; text: string; truncated?: boolean }
  | { kind: 'unsupported'; ext: string }

export default function DocumentPreview({ projectId, document: doc, emptyLabel }: Props) {
  const [data, setData] = useState<PreviewData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [activeSheet, setActiveSheet] = useState(0)

  // ── Fetch the JSON preview whenever the selected document changes ──────────
  useEffect(() => {
    if (!doc || !projectId) {
      setData(null)
      setError(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    setData(null)
    setActiveSheet(0)
    void (async () => {
      try {
        const resp = await apiGet<PreviewData>(
          `/v1/projects/${projectId}/documents/${doc.id}/preview`,
        )
        if (!cancelled) setData(resp)
      } catch (err) {
        if (cancelled) return
        const msg =
          err instanceof ApiError && err.status === 422
            ? 'This file could not be rendered for preview.'
            : err instanceof Error
              ? err.message
              : 'Could not load the preview.'
        setError(msg)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [projectId, doc])

  if (!doc) {
    return (
      <div className="doc-preview doc-preview--empty">
        {emptyLabel ?? 'Select a document to preview'}
      </div>
    )
  }

  if (loading) {
    return (
      <div className="doc-preview doc-preview--status">
        Loading preview of {doc.original_name}…
      </div>
    )
  }

  if (error) {
    return (
      <div className="doc-preview doc-preview--status" role="alert">
        {error}
      </div>
    )
  }

  if (!data) {
    return (
      <div className="doc-preview doc-preview--status">
        No preview available for {doc.original_name}.
      </div>
    )
  }

  return (
    <div className="doc-preview">
      <div className="doc-preview__title" title={doc.original_name}>
        {doc.original_name}
      </div>
      {data.kind === 'table' && (
        <TablePreview
          sheets={data.sheets}
          truncated={data.truncated}
          activeSheet={activeSheet}
          onSelectSheet={setActiveSheet}
        />
      )}
      {data.kind === 'pdf' && (
        <PdfPreview projectId={projectId} docId={doc.id} name={doc.original_name} />
      )}
      {data.kind === 'text' && (
        <div className="doc-preview__scroll">
          <pre className="doc-preview__text">{data.text}</pre>
          {data.truncated && (
            <p className="doc-preview__note">Preview truncated to the first part of the document.</p>
          )}
        </div>
      )}
      {data.kind === 'unsupported' && (
        <div className="doc-preview--status">
          Inline preview is not available for {data.ext || 'this'} files. Use the document list to download it.
        </div>
      )}
    </div>
  )
}

// ── Table ────────────────────────────────────────────────────────────────────

interface TablePreviewProps {
  sheets: Array<{ name: string; rows: string[][] }>
  truncated?: boolean
  activeSheet: number
  onSelectSheet: (i: number) => void
}

function TablePreview({ sheets, truncated, activeSheet, onSelectSheet }: TablePreviewProps) {
  if (sheets.length === 0) {
    return <div className="doc-preview--status">This spreadsheet has no sheets to show.</div>
  }
  const idx = Math.min(activeSheet, sheets.length - 1)
  const sheet = sheets[idx]
  const rows = sheet.rows
  const header = rows[0] ?? []
  const bodyRows = rows.slice(1)

  return (
    <>
      {sheets.length > 1 && (
        <div className="doc-preview__sheets" role="tablist">
          {sheets.map((s, i) => (
            <button
              key={`${s.name}-${i}`}
              type="button"
              role="tab"
              aria-selected={i === idx}
              className={
                'doc-preview__sheet-tab' +
                (i === idx ? ' doc-preview__sheet-tab--active' : '')
              }
              onClick={() => onSelectSheet(i)}
            >
              {s.name}
            </button>
          ))}
        </div>
      )}
      <div className="doc-preview__scroll">
        {rows.length === 0 ? (
          <div className="doc-preview--status">This sheet is empty.</div>
        ) : (
          <table className="doc-preview__table">
            <thead>
              <tr>
                {header.map((cell, c) => (
                  <th key={c}>{cell}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((row, r) => (
                <tr key={r}>
                  {header.map((_, c) => (
                    <td key={c}>{row[c] ?? ''}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {truncated && (
        <p className="doc-preview__note">
          Large sheet — showing the first rows and columns only.
        </p>
      )}
    </>
  )
}

// ── PDF ──────────────────────────────────────────────────────────────────────

interface PdfPreviewProps {
  projectId: string
  docId: string
  name: string
}

function PdfPreview({ projectId, docId, name }: PdfPreviewProps) {
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    setUrl(null)
    setError(null)
    void (async () => {
      try {
        const token = getToken()
        const res = await fetch(
          `${API_BASE}/v1/projects/${projectId}/documents/${docId}/preview/raw`,
          { headers: token ? { Authorization: `Bearer ${token}` } : {} },
        )
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const blob = await res.blob()
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load the PDF.')
      }
    })()
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [projectId, docId])

  if (error) {
    return <div className="doc-preview--status" role="alert">{error}</div>
  }
  if (!url) {
    return <div className="doc-preview--status">Loading document…</div>
  }
  return (
    <object className="doc-preview__pdf" data={url} type="application/pdf" aria-label={name}>
      <p className="doc-preview--status">
        This PDF cannot be displayed inline in your browser.
      </p>
    </object>
  )
}
