// Memory-UI-Block - Cache Statistics & Operations
// import { MemoryBlock } from './blocks/Memory'
// <MemoryBlock apiKey="cb_key" />

import { useState, useEffect } from 'react';

interface MemoryBlockProps {
  apiKey: string;
}

interface CacheStats {
  size: number;
  max_size: number;
  hits: number;
  misses: number;
  hit_rate: number;
  evictions: number;
}

export const MemoryBlock: React.FC<MemoryBlockProps> = ({ apiKey }) => {
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [cacheKey, setCacheKey] = useState('');
  const [cacheValue, setCacheValue] = useState('');
  const [ttl, setTtl] = useState('60');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const fetchStats = async () => {
    try {
      const response = await fetch(`${API_BASE}/v1/memory/stats`, {
        headers: { 'Authorization': `Bearer ${apiKey}` }
      });
      if (response.ok) {
        const data = await response.json();
        setStats(data);
      }
    } catch (error) {
      console.error('Failed to fetch stats');
    }
  };

  useEffect(() => {
    fetchStats();
    const interval = setInterval(fetchStats, 5000);
    return () => clearInterval(interval);
  }, [apiKey]);

  const setCache = async () => {
    if (!cacheKey.trim() || !cacheValue.trim()) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/memory/set`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ 
          key: cacheKey, 
          value: cacheValue,
          ttl: parseInt(ttl) || 60
        })
      });
      const data = await response.json();
      setResult(JSON.stringify(data, null, 2));
      fetchStats();
    } catch (error) {
      setResult('Error setting cache');
    } finally {
      setLoading(false);
    }
  };

  const getCache = async () => {
    if (!cacheKey.trim()) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/memory/get`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ key: cacheKey })
      });
      const data = await response.json();
      setResult(JSON.stringify(data, null, 2));
      if (data.value) {
        setCacheValue(typeof data.value === 'string' ? data.value : JSON.stringify(data.value));
      }
    } catch (error) {
      setResult('Error getting cache');
    } finally {
      setLoading(false);
    }
  };

  const deleteCache = async () => {
    if (!cacheKey.trim()) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/memory/delete`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ key: cacheKey })
      });
      const data = await response.json();
      setResult(JSON.stringify(data, null, 2));
      fetchStats();
    } catch (error) {
      setResult('Error deleting cache');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '15px' }}>
      {/* Stats */}
      {stats && (
        <div style={{ 
          display: 'grid', 
          gridTemplateColumns: 'repeat(3, 1fr)', 
          gap: '10px',
          marginBottom: '15px' 
        }}>
          <div style={{ padding: '10px', background: '#e3f2fd', borderRadius: '4px', textAlign: 'center' }}>
            <div style={{ fontSize: '20px', fontWeight: 'bold', color: '#1976d2' }}>
              {stats.size}/{stats.max_size}
            </div>
            <div style={{ fontSize: '11px', color: '#666' }}>Cache Size</div>
          </div>
          <div style={{ padding: '10px', background: '#e8f5e9', borderRadius: '4px', textAlign: 'center' }}>
            <div style={{ fontSize: '20px', fontWeight: 'bold', color: '#388e3c' }}>
              {stats.hit_rate}%
            </div>
            <div style={{ fontSize: '11px', color: '#666' }}>Hit Rate</div>
          </div>
          <div style={{ padding: '10px', background: '#fff3e0', borderRadius: '4px', textAlign: 'center' }}>
            <div style={{ fontSize: '20px', fontWeight: 'bold', color: '#f57c00' }}>
              {stats.evictions}
            </div>
            <div style={{ fontSize: '11px', color: '#666' }}>Evictions</div>
          </div>
        </div>
      )}

      {/* Operations */}
      <div style={{ marginBottom: '15px' }}>
        <h4 style={{ margin: '0 0 10px 0' }}>Cache Operations</h4>
        <div style={{ display: 'flex', gap: '10px', marginBottom: '10px' }}>
          <input
            type="text"
            value={cacheKey}
            onChange={(e) => setCacheKey(e.target.value)}
            placeholder="Key"
            style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
          />
          <input
            type="number"
            value={ttl}
            onChange={(e) => setTtl(e.target.value)}
            placeholder="TTL (s)"
            style={{ width: '80px', padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
          />
        </div>
        <textarea
          value={cacheValue}
          onChange={(e) => setCacheValue(e.target.value)}
          placeholder="Value (JSON or string)"
          rows={3}
          style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd', marginBottom: '10px', boxSizing: 'border-box' }}
        />
        <div style={{ display: 'flex', gap: '10px' }}>
          <button
            onClick={setCache}
            disabled={loading}
            style={{
              padding: '8px 16px',
              background: '#28a745',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer'
            }}
          >
            Set
          </button>
          <button
            onClick={getCache}
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
            Get
          </button>
          <button
            onClick={deleteCache}
            disabled={loading}
            style={{
              padding: '8px 16px',
              background: '#dc3545',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer'
            }}
          >
            Delete
          </button>
        </div>
      </div>

      {/* Result */}
      {result && (
        <pre style={{ 
          background: '#1e1e1e', 
          color: '#d4d4d4', 
          padding: '10px', 
          borderRadius: '4px',
          fontSize: '11px',
          overflow: 'auto',
          maxHeight: '150px'
        }}>
          {result}
        </pre>
      )}
    </div>
  );
};
