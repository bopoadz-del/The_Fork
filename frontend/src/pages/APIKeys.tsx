import { useState } from 'react'
import { Key, Copy, Eye, EyeOff, Trash2, Plus } from 'lucide-react'

interface APIKey {
  id: string
  name: string
  prefix: string
  tier: string
  createdAt: string
  lastUsed: string
  requests: number
}

function CreateKeyModal({ onClose, onCreate }: { onClose: () => void; onCreate: (name: string) => void }) {
  const [name, setName] = useState('')

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Create New API Key</h3>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}>
            ✕
          </button>
        </div>
        <div style={{ marginBottom: 20 }}>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Key Name</label>
          <input 
            type="text" 
            className="input" 
            placeholder="e.g., Production, Development"
            value={name}
            onChange={e => setName(e.target.value)}
          />
        </div>
        <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end' }}>
          <button className="btn btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={() => { onCreate(name); onClose(); }}>Create Key</button>
        </div>
      </div>
    </div>
  )
}

function KeyRow({ apiKey, onDelete }: { apiKey: APIKey; onDelete: (id: string) => void }) {
  const [showKey, setShowKey] = useState(false)
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(`cb_${apiKey.prefix}_xxxxxxxx`)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <tr>
      <td>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ 
            width: 40, 
            height: 40, 
            background: 'var(--bg-tertiary)', 
            borderRadius: 8,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--accent-primary)'
          }}>
            <Key size={20} />
          </div>
          <div>
            <div style={{ fontWeight: 500 }}>{apiKey.name}</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>Created {apiKey.createdAt}</div>
          </div>
        </div>
      </td>
      <td>
        <div className="key-display">
          <span className="key-masked">
            {showKey ? `cb_${apiKey.prefix}_xxxxxxxxxxxx` : `cb_${apiKey.prefix}_••••••••••••`}
          </span>
          <button 
            onClick={() => setShowKey(!showKey)}
            style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', padding: 4 }}
          >
            {showKey ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
          <button 
            onClick={handleCopy}
            style={{ background: 'none', border: 'none', color: copied ? 'var(--success)' : 'var(--text-secondary)', cursor: 'pointer', padding: 4 }}
          >
            <Copy size={16} />
          </button>
        </div>
      </td>
      <td>
        <span className={`badge ${apiKey.tier === 'pro' ? 'badge-warning' : 'badge-info'}`}>
          {apiKey.tier.toUpperCase()}
        </span>
      </td>
      <td>{apiKey.requests.toLocaleString()}</td>
      <td>{apiKey.lastUsed}</td>
      <td>
        <button 
          onClick={() => onDelete(apiKey.id)}
          style={{ background: 'none', border: 'none', color: 'var(--error)', cursor: 'pointer', padding: 8 }}
        >
          <Trash2 size={18} />
        </button>
      </td>
    </tr>
  )
}

export default function APIKeys() {
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [keys, setKeys] = useState<APIKey[]>([
    { id: '1', name: 'Production', prefix: 'prod', tier: 'pro', createdAt: 'Jan 15, 2025', lastUsed: '2 mins ago', requests: 12543 },
    { id: '2', name: 'Development', prefix: 'dev', tier: 'free', createdAt: 'Jan 10, 2025', lastUsed: '1 hour ago', requests: 892 },
    { id: '3', name: 'Mobile App', prefix: 'mobile', tier: 'pro', createdAt: 'Dec 28, 2024', lastUsed: '3 hours ago', requests: 3421 },
  ])

  const handleCreate = (name: string) => {
    const newKey: APIKey = {
      id: Date.now().toString(),
      name,
      prefix: name.toLowerCase().slice(0, 4),
      tier: 'free',
      createdAt: 'Just now',
      lastUsed: 'Never',
      requests: 0
    }
    setKeys([...keys, newKey])
  }

  const handleDelete = (id: string) => {
    setKeys(keys.filter(k => k.id !== id))
  }

  return (
    <div>
      <div className="page-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h1>API Keys</h1>
            <p>Manage your API keys and access tokens.</p>
          </div>
          <button className="btn btn-primary" onClick={() => setShowCreateModal(true)}>
            <Plus size={18} />
            Create New Key
          </button>
        </div>
      </div>

      <div className="card">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Key</th>
              <th>Tier</th>
              <th>Requests</th>
              <th>Last Used</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {keys.map(key => (
              <KeyRow key={key.id} apiKey={key} onDelete={handleDelete} />
            ))}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ marginTop: 24 }}>
        <div className="card-header">
          <h3 className="card-title">Security Tips</h3>
        </div>
        <div style={{ color: 'var(--text-secondary)', lineHeight: 1.8 }}>
          <p>• Never expose your API keys in client-side code or public repositories</p>
          <p>• Use environment variables to store your keys securely</p>
          <p>• Rotate your keys regularly - delete old keys and create new ones</p>
          <p>• Use separate keys for different environments (production, staging, development)</p>
          <p>• Monitor your usage regularly for any suspicious activity</p>
        </div>
      </div>

      {showCreateModal && (
        <CreateKeyModal onClose={() => setShowCreateModal(false)} onCreate={handleCreate} />
      )}
    </div>
  )
}
