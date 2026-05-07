// Translate-UI-Block
import { useState } from 'react';
import { CerebrumClient } from '../../api/client';

export const TranslateBlock: React.FC<{ apiKey: string }> = ({ apiKey }) => {
  const client = new CerebrumClient(apiKey);
  const [text, setText] = useState('');
  const [target, setTarget] = useState('en');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);

  const translate = async () => {
    if (!text) return;
    setLoading(true);
    try {
      const data = await client.execute('translate', text, { target_lang: target });
      setResult(data?.result?.translation || data?.result?.text || JSON.stringify(data, null, 2));
    } catch (err: any) {
      setResult('Error: ' + (err.message || 'Request failed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '10px' }}>
      <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Text to translate..." style={{ width: '100%', padding: '8px', height: '60px', marginBottom: '5px' }} />
      <div style={{ display: 'flex', gap: '10px' }}>
        <select value={target} onChange={(e) => setTarget(e.target.value)} style={{ padding: '8px' }}>
          <option value="en">English</option>
          <option value="es">Spanish</option>
          <option value="fr">French</option>
          <option value="de">German</option>
          <option value="zh">Chinese</option>
          <option value="ar">Arabic</option>
        </select>
        <button onClick={translate} disabled={loading} style={{ padding: '8px 16px' }}>{loading ? '...' : '🌐 Translate'}</button>
      </div>
      {result && <div style={{ marginTop: '10px', padding: '10px', background: '#e8f5e9', borderRadius: '4px' }}>{result}</div>}
    </div>
  );
};
