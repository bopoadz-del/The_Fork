// Voice-UI-Block - TTS/STT
import { useState } from 'react';
import { CerebrumClient } from '../../api/client';

export const VoiceBlock: React.FC<{ apiKey: string }> = ({ apiKey }) => {
  const client = new CerebrumClient(apiKey);
  const [text, setText] = useState('');
  const [mode, setMode] = useState<'tts' | 'stt'>('tts');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);

  const process = async () => {
    setLoading(true);
    try {
      const data = await client.execute('voice', text, { mode });
      setResult(data?.result?.audio_url || data?.result?.text || JSON.stringify(data, null, 2));
    } catch (err: any) {
      setResult('Error: ' + (err.message || 'Request failed'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '10px' }}>
      <div style={{ marginBottom: '10px' }}>
        <select value={mode} onChange={(e) => setMode(e.target.value as any)} style={{ padding: '8px', marginRight: '10px' }}>
          <option value="tts">Text to Speech</option>
          <option value="stt">Speech to Text</option>
        </select>
      </div>
      {mode === 'tts' ? (
        <>
          <textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Enter text..." style={{ width: '100%', padding: '8px', height: '80px', marginBottom: '5px' }} />
          <button onClick={process} disabled={loading} style={{ padding: '8px 16px' }}>{loading ? '...' : '🔊 Speak'}</button>
        </>
      ) : (
        <div style={{ padding: '20px', textAlign: 'center', background: '#f5f5f5', borderRadius: '4px' }}>
          <button style={{ padding: '12px 24px', borderRadius: '50%' }}>🎙️ Record</button>
        </div>
      )}
      {result && <div style={{ marginTop: '10px', padding: '10px', background: '#e8f5e9', borderRadius: '4px', fontSize: '12px' }}>{result}</div>}
    </div>
  );
};
