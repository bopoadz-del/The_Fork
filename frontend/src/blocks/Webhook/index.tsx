// Webhook-UI-Block - Incoming/Outgoing Webhooks
// import { WebhookBlock } from './blocks/Webhook'
// <WebhookBlock apiKey="cb_key" />

import { useState, useEffect } from 'react';

interface WebhookBlockProps {
  apiKey: string;
}

interface WebhookEvent {
  id: string;
  event: string;
  payload: any;
  timestamp: string;
  source: string;
}

export const WebhookBlock: React.FC<WebhookBlockProps> = ({ apiKey }) => {
  const [mode, setMode] = useState<'incoming' | 'outgoing'>('incoming');
  const [events, setEvents] = useState<WebhookEvent[]>([]);
  const [url, setUrl] = useState('');
  const [eventType, setEventType] = useState('user.created');
  const [payload, setPayload] = useState('{"user_id": "123", "email": "test@example.com"}');
  const [webhookUrl, setWebhookUrl] = useState('');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  // Generate webhook URL on mount
  useEffect(() => {
    setWebhookUrl(`${API_BASE}/v1/webhooks/receive?api_key=${apiKey.substring(0, 8)}...`);
  }, [apiKey, API_BASE]);

  const fetchEvents = async () => {
    try {
      const response = await fetch(`${API_BASE}/v1/execute`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          block: 'webhook',
          action: 'list_events',
          limit: 20
        })
      });
      const data = await response.json();
      setEvents(data.events || []);
    } catch (error) {
      console.error('Failed to fetch events');
    }
  };

  useEffect(() => {
    if (mode === 'incoming') {
      fetchEvents();
      const interval = setInterval(fetchEvents, 5000);
      return () => clearInterval(interval);
    }
  }, [mode, apiKey]);

  const sendWebhook = async () => {
    if (!url.trim()) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/execute`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          block: 'webhook',
          action: 'send',
          url: url,
          event: eventType,
          payload: JSON.parse(payload || '{}')
        })
      });
      const data = await response.json();
      setResult(JSON.stringify(data, null, 2));
    } catch (error) {
      setResult('Error: ' + (error as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '15px' }}>
      {/* Mode Toggle */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '15px' }}>
        <button
          onClick={() => setMode('incoming')}
          style={{
            flex: 1,
            padding: '8px',
            background: mode === 'incoming' ? '#007bff' : '#e9ecef',
            color: mode === 'incoming' ? 'white' : '#333',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer'
          }}
        >
          📥 Incoming
        </button>
        <button
          onClick={() => setMode('outgoing')}
          style={{
            flex: 1,
            padding: '8px',
            background: mode === 'outgoing' ? '#007bff' : '#e9ecef',
            color: mode === 'outgoing' ? 'white' : '#333',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer'
          }}
        >
          📤 Outgoing
        </button>
      </div>

      {mode === 'incoming' ? (
        <>
          {/* Webhook URL */}
          <div style={{ 
            padding: '10px', 
            background: '#e3f2fd', 
            borderRadius: '4px',
            marginBottom: '15px'
          }}>
            <div style={{ fontSize: '11px', color: '#666', marginBottom: '5px' }}>
              Your Webhook Endpoint
            </div>
            <code style={{ 
              display: 'block',
              padding: '8px',
              background: '#1e1e1e',
              color: '#d4d4d4',
              borderRadius: '4px',
              fontSize: '11px',
              wordBreak: 'break-all'
            }}>
              {webhookUrl}
            </code>
          </div>

          {/* Recent Events */}
          <h4 style={{ margin: '0 0 10px 0' }}>Recent Events ({events.length})</h4>
          <div style={{ maxHeight: '200px', overflow: 'auto' }}>
            {events.length === 0 ? (
              <p style={{ color: '#6c757d', fontStyle: 'italic' }}>
                No webhook events received yet
              </p>
            ) : (
              events.map((event, idx) => (
                <div key={idx} style={{
                  padding: '10px',
                  borderBottom: '1px solid #eee',
                  background: idx % 2 === 0 ? '#f8f9fa' : 'white'
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ 
                      padding: '2px 8px',
                      borderRadius: '12px',
                      fontSize: '10px',
                      background: '#007bff',
                      color: 'white'
                    }}>
                      {event.event}
                    </span>
                    <span style={{ fontSize: '11px', color: '#666' }}>
                      {new Date(event.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <div style={{ fontSize: '11px', color: '#666', marginTop: '5px' }}>
                    Source: {event.source}
                  </div>
                  <pre style={{ 
                    margin: '5px 0 0 0',
                    fontSize: '10px',
                    background: '#f8f9fa',
                    padding: '5px',
                    borderRadius: '4px',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis'
                  }}>
                    {JSON.stringify(event.payload).substring(0, 100)}...
                  </pre>
                </div>
              ))
            )}
          </div>
        </>
      ) : (
        <>
          {/* Outgoing Webhook Form */}
          <div style={{ marginBottom: '10px' }}>
            <label style={{ display: 'block', fontSize: '12px', marginBottom: '5px' }}>
              Target URL
            </label>
            <input
              type="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/webhook"
              style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd', boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ marginBottom: '10px' }}>
            <label style={{ display: 'block', fontSize: '12px', marginBottom: '5px' }}>
              Event Type
            </label>
            <input
              type="text"
              value={eventType}
              onChange={(e) => setEventType(e.target.value)}
              placeholder="user.created"
              style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd', boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ marginBottom: '10px' }}>
            <label style={{ display: 'block', fontSize: '12px', marginBottom: '5px' }}>
              Payload (JSON)
            </label>
            <textarea
              value={payload}
              onChange={(e) => setPayload(e.target.value)}
              rows={4}
              style={{ 
                width: '100%', 
                padding: '8px', 
                borderRadius: '4px', 
                border: '1px solid #ddd',
                fontFamily: 'monospace',
                fontSize: '12px',
                boxSizing: 'border-box'
              }}
            />
          </div>
          <button
            onClick={sendWebhook}
            disabled={loading || !url.trim()}
            style={{
              width: '100%',
              padding: '10px',
              background: '#28a745',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontWeight: 'bold'
            }}
          >
            {loading ? 'Sending...' : '📤 Send Webhook'}
          </button>
        </>
      )}

      {/* Result */}
      {result && (
        <pre style={{ 
          marginTop: '15px',
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
