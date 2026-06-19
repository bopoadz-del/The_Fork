/* Shared chat types — kept in their own file so chat/ components don't
 * have to round-trip through pages/ProjectWorkspace for the same shapes. */

export type MessageRole = 'user' | 'assistant'

export interface CitedSource {
  doc_id: string
  doc_name: string
  page_or_section: string
  score: number
  confidence: 'High' | 'Medium' | 'Low'
}

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  streaming?: boolean
  error?: boolean
  /** Transient tool-activity label shown while the agent is calling tools. */
  toolStatus?: string
  /** Top retrieved sources, populated by the SSE 'end' event when RAG fired. */
  sources?: CitedSource[]
}
