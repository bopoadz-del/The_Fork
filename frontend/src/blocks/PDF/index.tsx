// PDF-UI-Block - PDF text and table extraction
import { useState } from 'react';

interface PDFBlockProps {
  apiKey: string;
  onExtract?: (result: any) => void;
}

export const PDFBlock: React.FC<PDFBlockProps> = ({ apiKey, onExtract }) => {
  const [filePath, setFilePath] = useState('');
  const [extractType, setExtractType] = useState<'text' | 'tables' | 'metadata'>('text');
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const extract = async () => {
    if (!filePath) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/pdf/extract`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ file_path: filePath, extract: extractType })
      });
      const data = await response.json();
      setResult(data);
      onExtract?.(data);
    } catch (error) {
      console.error('PDF extraction failed:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '10px' }}>
      <div style={{ marginBottom: '10px' }}>
        <input type="text" value={filePath} onChange={(e) => setFilePath(e.target.value)} placeholder="PDF file path..." style={{ width: '100%', padding: '8px', marginBottom: '5px' }} />
        <select value={extractType} onChange={(e) => setExtractType(e.target.value as any)} style={{ padding: '8px', marginRight: '10px' }}>
          <option value="text">Text</option>
          <option value="tables">Tables</option>
          <option value="metadata">Metadata</option>
        </select>
        <button onClick={extract} disabled={loading} style={{ padding: '8px 16px' }}>{loading ? '...' : '📄 Extract'}</button>
      </div>
      {result && (
        <div style={{ padding: '10px', background: '#f5f5f5', borderRadius: '4px', maxHeight: '200px', overflow: 'auto', fontSize: '12px' }}>
          {result.text && <div>{result.text.substring(0, 500)}...</div>}
          {result.tables && <div>📊 {result.tables.length} tables</div>}
          {result.pages && <div>📄 {result.pages} pages</div>}
        </div>
      )}
    </div>
  );
};
