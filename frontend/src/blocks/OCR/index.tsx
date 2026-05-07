// OCR-UI-Block - Image text extraction
import { useState } from 'react';

interface OCRBlockProps {
  apiKey: string;
  onExtract?: (result: any) => void;
}

export const OCRBlock: React.FC<OCRBlockProps> = ({ apiKey, onExtract }) => {
  const [imagePath, setImagePath] = useState('');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const extract = async () => {
    if (!imagePath) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/ocr`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
        body: JSON.stringify({ image: imagePath, lang: 'eng' })
      });
      const data = await response.json();
      setResult(data);
      onExtract?.(data);
    } catch (error) {
      console.error('OCR failed:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '10px' }}>
      <div style={{ display: 'flex', gap: '10px', marginBottom: '10px' }}>
        <input type="text" value={imagePath} onChange={(e) => setImagePath(e.target.value)} placeholder="Image path..." style={{ flex: 1, padding: '8px' }} />
        <button onClick={extract} disabled={loading} style={{ padding: '8px 16px' }}>{loading ? '...' : '👁️ OCR'}</button>
      </div>
      {result?.text && (
        <div style={{ padding: '10px', background: '#f5f5f5', borderRadius: '4px', fontSize: '12px' }}>
          {result.text.substring(0, 500)}
        </div>
      )}
    </div>
  );
};
