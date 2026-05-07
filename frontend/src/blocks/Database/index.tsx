// Database-UI-Block - SQL/NoSQL Operations
// import { DatabaseBlock } from './blocks/Database'
// <DatabaseBlock apiKey="cb_key" />

import { useState } from 'react';

interface DatabaseBlockProps {
  apiKey: string;
}

export const DatabaseBlock: React.FC<DatabaseBlockProps> = ({ apiKey }) => {
  const [operation, setOperation] = useState('query');
  const [table, setTable] = useState('users');
  const [query, setQuery] = useState('SELECT * FROM users LIMIT 10');
  const [data, setData] = useState('');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const executeOperation = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/execute`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          block: 'database',
          action: operation,
          table: table,
          query: query,
          data: data ? JSON.parse(data) : undefined
        })
      });
      const responseData = await response.json();
      setResult(JSON.stringify(responseData, null, 2));
    } catch (error) {
      setResult('Error: ' + (error as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const operations = [
    { value: 'query', label: 'Query', desc: 'Execute SQL query' },
    { value: 'insert', label: 'Insert', desc: 'Insert records' },
    { value: 'update', label: 'Update', desc: 'Update records' },
    { value: 'delete', label: 'Delete', desc: 'Delete records' },
    { value: 'list_tables', label: 'List Tables', desc: 'Show all tables' }
  ];

  return (
    <div style={{ padding: '15px' }}>
      {/* Operation Selector */}
      <div style={{ marginBottom: '15px' }}>
        <label style={{ display: 'block', fontSize: '12px', marginBottom: '5px', fontWeight: 'bold' }}>
          Operation
        </label>
        <select
          value={operation}
          onChange={(e) => setOperation(e.target.value)}
          style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
        >
          {operations.map(op => (
            <option key={op.value} value={op.value}>{op.label} - {op.desc}</option>
          ))}
        </select>
      </div>

      {/* Table Input */}
      <div style={{ marginBottom: '15px' }}>
        <label style={{ display: 'block', fontSize: '12px', marginBottom: '5px', fontWeight: 'bold' }}>
          Table/Collection
        </label>
        <input
          type="text"
          value={table}
          onChange={(e) => setTable(e.target.value)}
          placeholder="users, orders, products..."
          style={{ width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #ddd', boxSizing: 'border-box' }}
        />
      </div>

      {/* Query Input */}
      {operation === 'query' && (
        <div style={{ marginBottom: '15px' }}>
          <label style={{ display: 'block', fontSize: '12px', marginBottom: '5px', fontWeight: 'bold' }}>
            SQL Query
          </label>
          <textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="SELECT * FROM users WHERE..."
            rows={3}
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
      )}

      {/* Data Input (for insert/update) */}
      {(operation === 'insert' || operation === 'update') && (
        <div style={{ marginBottom: '15px' }}>
          <label style={{ display: 'block', fontSize: '12px', marginBottom: '5px', fontWeight: 'bold' }}>
            Data (JSON)
          </label>
          <textarea
            value={data}
            onChange={(e) => setData(e.target.value)}
            placeholder={'{"name": "John", "email": "john@example.com"}'}
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
      )}

      {/* Execute Button */}
      <button
        onClick={executeOperation}
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
          marginBottom: '15px'
        }}
      >
        {loading ? 'Executing...' : 'Execute'}
      </button>

      {/* Result */}
      {result && (
        <div>
          <label style={{ display: 'block', fontSize: '12px', marginBottom: '5px', fontWeight: 'bold' }}>
            Result
          </label>
          <pre style={{ 
            background: '#1e1e1e', 
            color: '#d4d4d4', 
            padding: '10px', 
            borderRadius: '4px',
            fontSize: '11px',
            overflow: 'auto',
            maxHeight: '200px',
            margin: 0
          }}>
            {result}
          </pre>
        </div>
      )}

      {/* Supported Databases */}
      <div style={{ 
        marginTop: '15px', 
        padding: '10px', 
        background: '#e9ecef', 
        borderRadius: '4px',
        fontSize: '11px'
      }}>
        <strong>Supported:</strong> PostgreSQL, MySQL, SQLite, MongoDB, Redis
      </div>
    </div>
  );
};
