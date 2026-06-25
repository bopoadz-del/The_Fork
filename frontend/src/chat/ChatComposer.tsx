/* ChatComposer — Quarry design 2026-06-21.
 *
 * Changes vs PR #90:
 *   • The three inline icon buttons (Attach / Photo / Voice) collapse
 *     into a single "+" button that opens a popover with four options:
 *     Attach file · Google Drive · Photo · Voice.
 *   • The send button stays right-aligned with the ArrowUp glyph.
 *   • Optional Clear button still surfaces only when hasHistory.
 *
 * All file upload + voice recording behavior is preserved byte-for-byte;
 * the popover items dispatch to the same handlers as the old buttons.
 * Google Drive item invokes the parent-supplied onOpenDrive callback so
 * ProjectWorkspace can surface its DrivePanel as a modal.
 */
import { useEffect, useRef, useState } from 'react'
import {
  Plus, Paperclip, Camera, Mic, MicOff, RotateCcw, ArrowUp, Cloud,
} from 'lucide-react'
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
  /** Open the Google Drive picker (parent renders DrivePanel as a modal). */
  onOpenDrive?: () => void
}

export default function ChatComposer({
  onSend, disabled, disabledReason, projectId,
  onAttached, onClear, hasHistory, onOpenDrive,
}: Props) {
  const [text, setText] = useState('')
  const [uploading, setUploading] = useState(false)
  const [attachStatus, setAttachStatus] = useState<string | null>(null)
  const [recording, setRecording] = useState(false)
  const [popoverOpen, setPopoverOpen] = useState(false)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const popoverRootRef = useRef<HTMLDivElement>(null)

  // Close popover on outside click + Escape
  useEffect(() => {
    if (!popoverOpen) return
    function onDown(ev: MouseEvent) {
      if (popoverRootRef.current && !popoverRootRef.current.contains(ev.target as Node)) {
        setPopoverOpen(false)
      }
    }
    function onKey(ev: KeyboardEvent) {
      if (ev.key === 'Escape') setPopoverOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [popoverOpen])

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
      // Surface V2 safety/QA-QC detections when the upload endpoint ran
      // the image block inline on a photo.
      const sq = body?.safety_qaqc as { count: number; top: { class: string; confidence: number }[] } | undefined
      const detSummary = sq && sq.count > 0
        ? sq.top.map((d) => `${d.class}@${d.confidence.toFixed(2)}`).join(', ')
        : ''
      const statusMsg = detSummary
        ? `Attached: ${docName} — detected ${sq!.count}: ${detSummary}`
        : `Attached: ${docName}`
      setAttachStatus(statusMsg)
      onAttached?.(docName)
      const inlineTag = detSummary
        ? `[attached: ${docName} | safety_qaqc: ${detSummary}] `
        : `[attached: ${docName}] `
      setText((prev) => (prev ? `${prev}\n` : '') + inlineTag)
      setTimeout(() => setAttachStatus(null), 6000)
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
      setAttachStatus('Recording — click + then Voice again to stop')
    } catch (err) {
      setAttachStatus(`Mic blocked: ${(err as Error).message}`)
    }
  }

  function stopVoiceRecording() {
    mediaRecorderRef.current?.stop()
    mediaRecorderRef.current = null
    setRecording(false)
  }

  function pickAttach() {
    setPopoverOpen(false)
    fileInputRef.current?.click()
  }

  function pickPhoto() {
    setPopoverOpen(false)
    cameraInputRef.current?.click()
  }

  function pickDrive() {
    setPopoverOpen(false)
    onOpenDrive?.()
  }

  function pickVoice() {
    setPopoverOpen(false)
    if (recording) stopVoiceRecording()
    else startVoiceRecording()
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
          <div className="chat-composer__plus-wrap" ref={popoverRootRef}>
            <button
              type="button"
              className={`chat-composer__plus${popoverOpen ? ' chat-composer__plus--open' : ''}`}
              title="Attach or record"
              onClick={() => setPopoverOpen((v) => !v)}
              disabled={disabled || uploading}
              aria-haspopup="menu"
              aria-expanded={popoverOpen}
              aria-label="Open attachment menu"
            >
              <Plus size={16} />
            </button>
            {popoverOpen && (
              <div className="chat-composer__popover" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  className="chat-composer__popover-item"
                  onClick={pickAttach}
                >
                  <Paperclip size={14} />
                  <span>Attach file</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="chat-composer__popover-item"
                  onClick={pickDrive}
                  disabled={!onOpenDrive}
                >
                  <Cloud size={14} />
                  <span>Google Drive</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className="chat-composer__popover-item"
                  onClick={pickPhoto}
                >
                  <Camera size={14} />
                  <span>Photo</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={
                    'chat-composer__popover-item' +
                    (recording ? ' chat-composer__popover-item--rec' : '')
                  }
                  onClick={pickVoice}
                >
                  {recording ? <MicOff size={14} /> : <Mic size={14} />}
                  <span>{recording ? 'Stop recording' : 'Voice'}</span>
                </button>
              </div>
            )}
          </div>

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
