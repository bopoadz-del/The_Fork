// Failover-UI-Block - System health dashboard
import { useState, useEffect } from 'react';

interface FailoverBlockProps {
  apiKey: string;
}

export const FailoverBlock: React.FC<FailoverBlockProps> = ({ apiKey: _apiKey }) => {
  const [status, setStatus] = useState<any>(null);
  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const response = await fetch(`${API_BASE}/v1/health`);
        const data = await response.json();
        setStatus(data);
      } catch (e) {
        console.error('Failed to fetch status');
      }
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 5000);
    return () => clearInterval(interval);
  }, []);

  if (!status) return <div>Loading...</div>;

  return (
    <div style={{ padding: '10px' }}>
      <div style={{ marginBottom: '10px', padding: '10px', background: '#f5f5f5', borderRadius: '4px' }}>
        <strong>System Health:</strong> {status.status}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5px' }}>
        {Object.entries(status.blocks || {}).map(([name, block]: [string, any]) => (
          <div key={name} style={{ 
            padding: '8px', 
            background: block.status === 'healthy' ? '#e8f5e9' : '#ffebee',
            borderRadius: '4px',
            fontSize: '12px'
          }}>
            <strong>{name}</strong>: {block.status}
          </div>
        ))}
      </div>
    </div>
  );
};
