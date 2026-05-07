// AndroidDrive-UI-Block
import { useState } from 'react';
import { CerebrumClient } from '../../api/client';

export const AndroidDriveBlock: React.FC<{ apiKey: string }> = ({ apiKey }) => {
  const client = new CerebrumClient(apiKey);
  const [message, setMessage] = useState('Connect Android device to access files');
  const [loading, setLoading] = useState(false);

  const connect = async () => {
    setLoading(true);
    try {
      const data = await client.execute('android_drive', null, { action: 'list' });
      setMessage(`Connected. Found ${(data.files || []).length} files.`);
    } catch (err: any) {
      setMessage('Error: ' + (err.message || 'Request failed'));
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '10px', border: '1px solid #ddd', borderRadius: '4px' }}>
      <div style={{ padding: '10px', background: '#f5f5f5', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>📱 Android Drive</span>
        <button onClick={connect} disabled={loading} style={{ padding: '4px 8px', fontSize: '11px' }}>{loading ? '...' : 'Connect'}</button>
      </div>
      <div style={{ padding: '10px', color: '#666' }}>{message}</div>
    </div>
  );
};
