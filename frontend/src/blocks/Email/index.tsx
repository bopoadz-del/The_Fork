// Email-UI-Block - Send/Receive Emails
// import { EmailBlock } from './blocks/Email'
// <EmailBlock apiKey="cb_key" />

import { useState } from 'react';

interface EmailBlockProps {
  apiKey: string;
}

export const EmailBlock: React.FC<EmailBlockProps> = ({ apiKey }) => {
  const [mode, setMode] = useState<'send' | 'receive'>('send');
  const [to, setTo] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);
  const [emails, setEmails] = useState<any[]>([]);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const sendEmail = async () => {
    if (!to.trim() || !subject.trim() || !body.trim()) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/execute`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          block: 'email',
          action: 'send',
          to: to,
          subject: subject,
          body: body,
          html: false
        })
      });
      const data = await response.json();
      setResult(JSON.stringify(data, null, 2));
      if (data.sent) {
        setTo('');
        setSubject('');
        setBody('');
      }
    } catch (error) {
      setResult('Error: ' + (error as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const receiveEmails = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/execute`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          block: 'email',
          action: 'receive',
          limit: 10
        })
      });
      const data = await response.json();
      setEmails(data.emails || []);
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
          onClick={() => setMode('send')}
          style={{
            flex: 1,
            padding: '8px',
            background: mode === 'send' ? '#007bff' : '#e9ecef',
            color: mode === 'send' ? 'white' : '#333',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer'
          }}
        >
          ✉️ Send Email
        </button>
        <button
          onClick={() => setMode('receive')}
          style={{
            flex: 1,
            padding: '8px',
            background: mode === 'receive' ? '#007bff' : '#e9ecef',
            color: mode === 'receive' ? 'white' : '#333',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer'
          }}
        >
          📥 Inbox
        </button>
      </div>

      {mode === 'send' ? (
        <>
          {/* Send Form */}
          <div style={{ marginBottom: '10px' }}>
            <input
              type="email"
              value={to}
              onChange={(e) => setTo(e.target.value)}
              placeholder="To: recipient@example.com"
              style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd', boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ marginBottom: '10px' }}>
            <input
              type="text"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Subject"
              style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd', boxSizing: 'border-box' }}
            />
          </div>
          <div style={{ marginBottom: '10px' }}>
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="Email body..."
              rows={5}
              style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd', boxSizing: 'border-box' }}
            />
          </div>
          <button
            onClick={sendEmail}
            disabled={loading || !to.trim() || !subject.trim() || !body.trim()}
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
            {loading ? 'Sending...' : '📤 Send Email'}
          </button>
        </>
      ) : (
        <>
          {/* Inbox */}
          <button
            onClick={receiveEmails}
            disabled={loading}
            style={{
              width: '100%',
              padding: '10px',
              background: '#007bff',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: loading ? 'not-allowed' : 'pointer',
              fontWeight: 'bold',
              marginBottom: '10px'
            }}
          >
            {loading ? 'Loading...' : '📥 Fetch Emails'}
          </button>

          <div style={{ maxHeight: '250px', overflow: 'auto' }}>
            {emails.length === 0 ? (
              <p style={{ color: '#6c757d', fontStyle: 'italic', textAlign: 'center' }}>
                No emails fetched yet
              </p>
            ) : (
              emails.map((email, idx) => (
                <div key={idx} style={{
                  padding: '10px',
                  borderBottom: '1px solid #eee',
                  background: idx % 2 === 0 ? '#f8f9fa' : 'white',
                  cursor: 'pointer'
                }}>
                  <div style={{ fontWeight: 'bold', fontSize: '13px' }}>{email.subject}</div>
                  <div style={{ fontSize: '11px', color: '#6c757d' }}>
                    From: {email.from} • {new Date(email.date).toLocaleString()}
                  </div>
                  <div style={{ fontSize: '12px', marginTop: '5px', color: '#333' }}>
                    {email.body?.substring(0, 100)}...
                  </div>
                </div>
              ))
            )}
          </div>
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

      {/* Providers */}
      <div style={{ 
        marginTop: '15px', 
        padding: '10px', 
        background: '#e9ecef', 
        borderRadius: '4px',
        fontSize: '11px'
      }}>
        <strong>Supported:</strong> SMTP, SendGrid, AWS SES, Gmail API
      </div>
    </div>
  );
};
