/* ChatComposer — floating input bar pinned to the bottom of the main
 * column. Behaviour preserved exactly from the pre-redesign component:
 *   • textarea auto-grows up to 180px
 *   • Enter sends; Shift+Enter newline
 *   • Attach (file picker), Photo (camera), Voice (MediaRecorder webm)
 *   • Optional Clear button when chat history exists
 *   • Upload status surfaces via attachStatus toast above the input
 *
 * The new visual treatment is a card pinned to the bottom of the
 * scrollable chat area — not full-bleed.
 */
import { useRef, useState } from 'react'
import { Mic, MicOff, Paperclip, Camera, RotateCcw, ArrowUp } from 'lucide-react'
import { getToken } from '../lib/token'
import './ChatComposer.css'

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? 'http://localhost:8000'

interface Props {
  onSend: (text: string) => void
  disabled: boolean
  disabledReason?: string
  projectId: string
  onAttached?: (docName: string) => void
  onClear?: () => void
  hasHistory?: boolean
}

export default function ChatComposer({
  onSend, disabled, disabledReason, projectId, onAttached, onClear, hasHistory,
}: Props) {
  const [text, setText] = useState('')
  const [uploading, setUploading] = useState(false)
  const [attachStatus, setAttachStatus] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])

  function submit() {
    const trimmed = text.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setText('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  function handleInput(e: React.ChangeEvent<HTMLTextAreaElement>) {
    setText(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }

  async function uploadFile(file: File, role = 'other') {
    setUploading(true)
    setAttachStatus(`Uploading ${file.name}…`)
    try {
      const token = getToken() || ''
      const fd = new FormData()
      fd.append('file', file)
      fd.append('role', role)
      const res = await fetch(`${API_BASE}/v1/projects/${projectId}/documents`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      })
      if (!res.ok) {
        const errBody = await res.text()
        setAttachStatus(`Upload failed (${res.status}): ${errBody.slice(0, 120)}`)
        return
      }
      const body = await res.json()
      const docName = body?.document?.original_name || file.name
      setAttachStatus(`Attached: ${docName}`)
      onAttached?.(docName)
      setText((prev) => (prev ? `${prev}\n` : '') + `[attached: ${docName}] `)
      setTimeout(() => setAttachStatus(null), 4000)
    } catch (err) {
      setAttachStatus(`Upload error: ${(err as Error).message}`)
    } finally {
      setUploading(false)
    }
  }

  async function startVoiceRecording() {
    if (recording) return
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      audioChunksRef.current = []
      mr.ondataavailable = (ev) => {
        if (ev.data.size > 0) audioChunksRef.current.push(ev.data)
      }
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        const file = new File([blob], `voice-${Date.now()}.webm`, { type: 'audio/webm' })
        await uploadFile(file, 'other')
      }
      mediaRecorderRef.current = mr
      mr.start()
      setRecording(true)
      setAttachStatus('Recording — click Stop to finish')
    } catch (err) {
      setAttachStatus(`Mic blocked: ${(err as Error).message}`)
    }
  }

  function stopVoiceRecording() {
    mediaRecorderRef.current?.stop()
    mediaRecorderRef.current = null
    setRecording(false)
  }

  return (
    <div className="chat-composer">
      {attachStatus && (
        <p className="chat-composer__attach-status" aria-live="polite">{attachStatus}</p>
      )}
      <div className="chat-composer__card">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          style={{ display: 'none' }}
          accept=".pdf,.docx,.doc,.xlsx,.xls,.csv,.txt,.md,.png,.jpg,.jpeg,.webp,.tif,.tiff,.dxf,.ifc,.xer,.mp3,.wav,.webm,.mp4"
          onChange={(e) => {
            const files = e.target.files
            if (files) Array.from(files).forEach((f) => uploadFile(f))
            e.target.value = ''
          }}
        />
        <input
          ref={cameraInputRef}
          type="file"
          accept="image/*"
          capture="environment"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) uploadFile(f)
            e.target.value = ''
          }}
        />

        <textarea
          ref={textareaRef}
          className="chat-composer__textarea"
          value={text}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder="Ask about your project documents..."
          disabled={disabled}
          rows={1}
          aria-label="Chat message"
        />

        <div className="chat-composer__row">
          <button
            type="button"
            className="chat-composer__tool"
            title="Attach file"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || uploading}
            aria-label="Attach file"
          >
            <Paperclip size={16} />
          </button>
          <button
            type="button"
            className="chat-composer__tool"
            title="Take photo"
            onClick={() => cameraInputRef.current?.click()}
            disabled={disabled || uploading}
            aria-label="Take photo"
          >
            <Camera size={16} />
          </button>
          <button
            type="button"
            className={`chat-composer__tool${recording ? ' chat-composer__tool--rec' : ''}`}
            title={recording ? 'Stop recording' : 'Voice note'}
            onClick={() => (recording ? stopVoiceRecording() : startVoiceRecording())}
            disabled={disabled || uploading}
            aria-label={recording ? 'Stop recording' : 'Record voice'}
          >
            {recording ? <MicOff size={16} /> : <Mic size={16} />}
          </button>
          {onClear && hasHistory && (
            <button
              type="button"
              className="chat-composer__tool"
              title="Clear chat history (cannot be undone)"
              onClick={() => onClear()}
              disabled={disabled || uploading}
              aria-label="Clear chat history"
            >
              <RotateCcw size={16} />
            </button>
          )}

          <span className="chat-composer__spacer" aria-hidden="true" />

          <span className="chat-composer__hint">Enter to send · Shift+Enter newline</span>

          <button
            type="button"
            className="chat-composer__send"
            onClick={submit}
            disabled={disabled || !text.trim()}
            aria-label="Send message"
            title={disabled && disabledReason ? disabledReason : undefined}
          >
            <ArrowUp size={16} />
          </button>
        </div>
      </div>
    </div>
  )
}
