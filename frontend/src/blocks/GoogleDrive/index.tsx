// GoogleDrive-UI-Block
import { useState } from 'react';
import { CerebrumClient } from '../../api/client';

interface GoogleDriveBlockProps {
  apiKey: string;
  onFileSelect?: (file: any) => void;
}

export const GoogleDriveBlock: React.FC<GoogleDriveBlockProps> = ({ apiKey, onFileSelect }) => {
  const client = new CerebrumClient(apiKey);
  const [files, setFiles] = useState<any[]>([{ name: 'document.pdf' }, { name: 'image.jpg' }]);
  const [loading, setLoading] = useState(false);

  const listFiles = async () => {
    setLoading(true);
    try {
      const data = await client.execute('google_drive', null, { action: 'list' });
      setFiles(data?.result?.files || data?.files || [{ name: 'document.pdf' }, { name: 'image.jpg' }]);
    } catch (err: any) {
      console.error('Failed to list files', err);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '10px', border: '1px solid #ddd', borderRadius: '4px' }}>
      <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd', marginBottom: '10px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>📁 Google Drive</span>
        <button onClick={listFiles} disabled={loading} style={{ padding: '4px 8px', fontSize: '12px' }}>{loading ? '...' : 'Refresh'}</button>
      </div>
      {files.map((f, i) => (
        <div key={i} onClick={() => onFileSelect?.(f)} style={{ padding: '8px', cursor: 'pointer' }}>📄 {f.name}</div>
      ))}
    </div>
  );
};
