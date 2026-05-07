// Workflow-UI-Block - Workflow Orchestration
// import { WorkflowBlock } from './blocks/Workflow'
// <WorkflowBlock apiKey="cb_key" />

import { useState } from 'react';

interface WorkflowBlockProps {
  apiKey: string;
}

interface WorkflowStep {
  id: string;
  block: string;
  action: string;
  params: Record<string, any>;
}

const availableBlocks = [
  { value: 'chat', label: '💬 Chat', actions: ['complete', 'stream'] },
  { value: 'vector', label: '🔍 Vector', actions: ['add', 'search'] },
  { value: 'pdf', label: '📄 PDF', actions: ['extract', 'parse'] },
  { value: 'ocr', label: '👁️ OCR', actions: ['extract'] },
  { value: 'web', label: '🕸️ Web', actions: ['scrape', 'browse'] },
  { value: 'email', label: '📧 Email', actions: ['send'] },
  { value: 'database', label: '🗄️ Database', actions: ['query', 'insert'] }
];

export const WorkflowBlock: React.FC<WorkflowBlockProps> = ({ apiKey }) => {
  const [workflowName, setWorkflowName] = useState('My Workflow');
  const [steps, setSteps] = useState<WorkflowStep[]>([]);
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);
  
  // New step form
  const [selectedBlock, setSelectedBlock] = useState('chat');
  const [selectedAction, setSelectedAction] = useState('complete');
  const [stepParams, setStepParams] = useState('{}');

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const addStep = () => {
    const newStep: WorkflowStep = {
      id: `step_${steps.length + 1}`,
      block: selectedBlock,
      action: selectedAction,
      params: JSON.parse(stepParams || '{}')
    };
    setSteps([...steps, newStep]);
    setStepParams('{}');
  };

  const removeStep = (index: number) => {
    setSteps(steps.filter((_, i) => i !== index));
  };

  const runWorkflow = async () => {
    if (steps.length === 0) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/chain`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          name: workflowName,
          steps: steps
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

  const currentBlock = availableBlocks.find(b => b.value === selectedBlock);

  return (
    <div style={{ padding: '15px' }}>
      {/* Workflow Name */}
      <div style={{ marginBottom: '15px' }}>
        <input
          type="text"
          value={workflowName}
          onChange={(e) => setWorkflowName(e.target.value)}
          placeholder="Workflow name"
          style={{ 
            width: '100%', 
            padding: '8px', 
            borderRadius: '4px', 
            border: '1px solid #ddd',
            fontWeight: 'bold',
            boxSizing: 'border-box'
          }}
        />
      </div>

      {/* Add Step */}
      <div style={{ 
        padding: '15px', 
        background: '#f8f9fa', 
        borderRadius: '4px',
        marginBottom: '15px'
      }}>
        <h5 style={{ margin: '0 0 10px 0' }}>Add Step</h5>
        <div style={{ display: 'flex', gap: '10px', marginBottom: '10px' }}>
          <select
            value={selectedBlock}
            onChange={(e) => {
              setSelectedBlock(e.target.value);
              const block = availableBlocks.find(b => b.value === e.target.value);
              setSelectedAction(block?.actions[0] || '');
            }}
            style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
          >
            {availableBlocks.map(b => (
              <option key={b.value} value={b.value}>{b.label}</option>
            ))}
          </select>
          <select
            value={selectedAction}
            onChange={(e) => setSelectedAction(e.target.value)}
            style={{ flex: 1, padding: '8px', borderRadius: '4px', border: '1px solid #ddd' }}
          >
            {currentBlock?.actions.map(a => (
              <option key={a} value={a}>{a}</option>
            ))}
          </select>
        </div>
        <textarea
          value={stepParams}
          onChange={(e) => setStepParams(e.target.value)}
          placeholder='{"message": "Hello", "provider": "deepseek"}'
          rows={2}
          style={{ 
            width: '100%', 
            padding: '8px', 
            borderRadius: '4px', 
            border: '1px solid #ddd',
            fontFamily: 'monospace',
            fontSize: '11px',
            marginBottom: '10px',
            boxSizing: 'border-box'
          }}
        />
        <button
          onClick={addStep}
          style={{
            width: '100%',
            padding: '8px',
            background: '#6c757d',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer'
          }}
        >
          ➕ Add Step
        </button>
      </div>

      {/* Steps List */}
      <div style={{ marginBottom: '15px' }}>
        <h5 style={{ margin: '0 0 10px 0' }}>Steps ({steps.length})</h5>
        {steps.length === 0 ? (
          <p style={{ color: '#6c757d', fontStyle: 'italic' }}>No steps added yet</p>
        ) : (
          <div style={{ maxHeight: '150px', overflow: 'auto' }}>
            {steps.map((step, idx) => (
              <div key={idx} style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                padding: '8px',
                background: idx % 2 === 0 ? '#e3f2fd' : '#f3e5f5',
                borderRadius: '4px',
                marginBottom: '5px',
                fontSize: '12px'
              }}>
                <div>
                  <strong>{idx + 1}.</strong>{' '}
                  {availableBlocks.find(b => b.value === step.block)?.label} → {step.action}
                </div>
                <button
                  onClick={() => removeStep(idx)}
                  style={{
                    padding: '2px 6px',
                    background: '#dc3545',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    fontSize: '10px'
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Run Button */}
      <button
        onClick={runWorkflow}
        disabled={loading || steps.length === 0}
        style={{
          width: '100%',
          padding: '10px',
          background: steps.length === 0 ? '#6c757d' : '#007bff',
          color: 'white',
          border: 'none',
          borderRadius: '4px',
          cursor: loading || steps.length === 0 ? 'not-allowed' : 'pointer',
          fontWeight: 'bold',
          marginBottom: '10px'
        }}
      >
        {loading ? 'Running...' : steps.length === 0 ? 'Add Steps to Run' : '▶️ Run Workflow'}
      </button>

      {/* Result */}
      {result && (
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
      )}
    </div>
  );
};
