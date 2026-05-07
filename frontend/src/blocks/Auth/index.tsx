// Auth-UI-Block - API Key Management & RBAC
// import { AuthBlock } from './blocks/Auth'
// <AuthBlock apiKey="cb_admin_key" />

import { useState, useEffect } from 'react';

interface AuthBlockProps {
  apiKey: string;
}

interface ApiKey {
  key: string;
  name: string;
  role: string;
  created_at: string;
  last_used?: string;
}

export const AuthBlock: React.FC<AuthBlockProps> = ({ apiKey }) => {
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [newKeyName, setNewKeyName] = useState('');
  const [newKeyRole, setNewKeyRole] = useState('basic');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const fetchKeys = async () => {
    try {
      const response = await fetch(`${API_BASE}/v1/auth/keys`, {
        headers: { 'Authorization': `Bearer ${apiKey}` }
      });
      if (response.ok) {
        const data = await response.json();
        setKeys(data.keys || []);
      }
    } catch (error) {
      console.error('Failed to fetch keys');
    }
  };

  useEffect(() => {
    fetchKeys();
  }, [apiKey]);

  const createKey = async () => {
    if (!newKeyName.trim()) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/auth/keys`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ name: newKeyName, role: newKeyRole })
      });
      const data = await response.json();
      if (response.ok) {
        setMessage(`Created: ${data.api_key}`);
        setNewKeyName('');
        fetchKeys();
      } else {
        setMessage(data.error || 'Failed to create key');
      }
    } catch (error) {
      setMessage('Error creating key');
    } finally {
      setLoading(false);
    }
  };

  const deleteKey = async (keyToDelete: string) => {
    try {
      await fetch(`${API_BASE}/v1/auth/keys/${keyToDelete}`, {
        method: 'DELETE',
        headers: { 'Authorization': `Bearer ${apiKey}` }
      });
      fetchKeys();
    } catch (error) {
      console.error('Failed to delete key');
    }
  };

  const getRoleColor = (role: string) => {
    switch (role) {
      case 'admin': return '#dc3545';
      case 'pro': return '#007bff';
      case 'basic': return '#28a745';
      case 'readonly': return '#6c757d';
      default: return '#6c757d';
    }
  };

  return (
    <div style={{ padding: '15px' }}>
      <div style={{ marginBottom: '15px' }}>
        <h4 style={{ margin: '0 0 10px 0' }}>Create New API Key</h4>
        <div style={{ display: 'flex', gap: '10px', marginBottom: '10px' }}>
          <input
            type="text"
            value={newKeyName}
            onChange={(e) => setNewKeyName(e.target.value)}
            placeholder="Key name (e.g., production)"
            style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
          />
          <select
            value={newKeyRole}
            onChange={(e) => setNewKeyRole(e.target.value)}
            style={{ padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
          >
            <option value="admin">Admin (1M/hr)</option>
            <option value="pro">Pro (50K/hr)</option>
            <option value="basic">Basic (1K/hr)</option>
            <option value="readonly">Readonly (500/hr)</option>
          </select>
          <button
            onClick={createKey}
            disabled={loading}
            style={{
              padding: '8px 16px',
              background: '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer'
            }}
          >
            {loading ? 'Creating...' : 'Create'}
          </button>
        </div>
        {message && (
          <div style={{ 
            padding: '8px', 
            background: message.includes('Created') ? '#d4edda' : '#f8d7da',
            borderRadius: '4px',
            fontSize: '12px',
            wordBreak: 'break-all'
          }}>
            {message}
          </div>
        )}
      </div>

      <div>
        <h4 style={{ margin: '0 0 10px 0' }}>Active API Keys ({keys.length})</h4>
        <div style={{ maxHeight: '200px', overflow: 'auto' }}>
          {keys.length === 0 ? (
            <p style={{ color: '#6c757d', fontStyle: 'italic' }}>No API keys found</p>
          ) : (
            keys.map((k, idx) => (
              <div key={idx} style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '10px',
                borderBottom: '1px solid #eee',
                background: idx % 2 === 0 ? '#f8f9fa' : 'white'
              }}>
                <div>
                  <div style={{ fontWeight: 'bold' }}>{k.name}</div>
                  <div style={{ fontSize: '11px', color: '#6c757d' }}>
                    {k.key.substring(0, 20)}... • Created: {new Date(k.created_at).toLocaleDateString()}
                  </div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{
                    padding: '2px 8px',
                    borderRadius: '12px',
                    fontSize: '11px',
                    fontWeight: 'bold',
                    color: 'white',
                    background: getRoleColor(k.role)
                  }}>
                    {k.role.toUpperCase()}
                  </span>
                  <button
                    onClick={() => deleteKey(k.key)}
                    style={{
                      padding: '4px 8px',
                      background: '#dc3545',
                      color: 'white',
                      border: 'none',
                      borderRadius: '4px',
                      cursor: 'pointer',
                      fontSize: '11px'
                    }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div style={{ marginTop: '15px', padding: '10px', background: '#e9ecef', borderRadius: '4px', fontSize: '12px' }}>
        <strong>Rate Limits:</strong>
        <div style={{ marginTop: '5px' }}>
          <span style={{ color: '#dc3545' }}>● Admin</span> 1M/hr • 
          <span style={{ color: '#007bff' }}> ● Pro</span> 50K/hr • 
          <span style={{ color: '#28a745' }}> ● Basic</span> 1K/hr • 
          <span style={{ color: '#6c757d' }}> ● Readonly</span> 500/hr
        </div>
      </div>
    </div>
  );
};
