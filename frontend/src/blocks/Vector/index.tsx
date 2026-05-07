// Vector-UI-Block - Semantic search interface
// <VectorBlock apiKey="cb_key" onResultsSelect={(r) => console.log(r)} />

import { useState } from 'react';

interface VectorBlockProps {
  apiKey: string;
  onResultsSelect?: (results: string[]) => void;
  placeholder?: string;
}

export const VectorBlock: React.FC<VectorBlockProps> = ({ 
  apiKey, 
  onResultsSelect,
  placeholder = "Search documents..."
}) => {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const search = async () => {
    if (!query.trim()) return;
    setLoading(true);

    try {
      const response = await fetch(`${API_BASE}/v1/vector/search`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ query, n_results: 5 })
      });

      const data = await response.json();
      setResults(data.results || []);
      onResultsSelect?.(data.results || []);
    } catch (error) {
      console.error('Vector search failed:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="vector-block" style={{ padding: '10px' }}>
      <div style={{ display: 'flex', gap: '10px', marginBottom: '10px' }}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyPress={(e) => e.key === 'Enter' && search()}
          placeholder={placeholder}
          style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
        />
        <button 
          onClick={search}
          disabled={loading}
          style={{ padding: '8px 16px', background: '#28a745', color: 'white', border: 'none', borderRadius: '4px' }}
        >
          {loading ? '...' : 'Search'}
        </button>
      </div>
      
      {results.length > 0 && (
        <div className="search-results" style={{ border: '1px solid #eee', borderRadius: '4px' }}>
          {results.map((result, idx) => (
            <div 
              key={idx} 
              onClick={() => onResultsSelect?.([result])}
              style={{ 
                padding: '10px', 
                borderBottom: '1px solid #eee',
                cursor: 'pointer'
              }}
            >
              {result.substring(0, 100)}...
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
