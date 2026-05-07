// Blocks Demo Page - Show all UI blocks snapping together
import { ChatBlock, VectorBlock, StorageBlock, BIMBlock, DroneBlock } from '../blocks';

export default function BlocksDemo() {
  const apiKey = 'cb_dev_key'; // In production, get from auth context

  return (
    <div className="blocks-demo" style={{ padding: '20px' }}>
      <h1>🧠 Cerebrum UI Blocks</h1>
      <p>Each UI block connects to its backend counterpart via REST API</p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        {/* Chat Block */}
        <div className="block-card" style={{ border: '1px solid #ddd', borderRadius: '8px', padding: '15px' }}>
          <h3>💬 Chat Block</h3>
          <ChatBlock apiKey={apiKey} provider="deepseek" streaming={true} />
        </div>

        {/* Vector Search Block */}
        <div className="block-card" style={{ border: '1px solid #ddd', borderRadius: '8px', padding: '15px' }}>
          <h3>🔍 Vector Search</h3>
          <VectorBlock 
            apiKey={apiKey} 
            placeholder="Search documents..."
            onResultsSelect={(r) => console.log('Selected:', r)}
          />
        </div>

        {/* Storage Block */}
        <div className="block-card" style={{ border: '1px solid #ddd', borderRadius: '8px', padding: '15px' }}>
          <h3>💾 Storage</h3>
          <StorageBlock 
            apiKey={apiKey}
            onFileSelect={(f) => console.log('File:', f)}
          />
        </div>

        {/* BIM Block */}
        <div className="block-card" style={{ border: '1px solid #ddd', borderRadius: '8px', padding: '15px' }}>
          <h3>🏗️ BIM Model</h3>
          <BIMBlock 
            apiKey={apiKey}
            projectId="demo_project"
            onElementSelect={(e) => console.log('Element:', e)}
          />
        </div>

        {/* Drone Block - Full width */}
        <div className="block-card" style={{ border: '1px solid #ddd', borderRadius: '8px', padding: '15px', gridColumn: 'span 2' }}>
          <h3>🚁 Drone Vision</h3>
          <DroneBlock 
            apiKey={apiKey}
            projectId="demo_project"
            onDefectFound={(d) => console.log('Defect:', d)}
          />
        </div>
      </div>

      <div style={{ marginTop: '30px', padding: '15px', background: '#f5f5f5', borderRadius: '8px' }}>
        <h4>Block Usage (3 lines of code each):</h4>
        <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: '15px', borderRadius: '4px', overflow: 'auto' }}>
{`import { ChatBlock, VectorBlock, BIMBlock } from './blocks';

// Each block auto-connects to backend API
<ChatBlock apiKey="cb_key" provider="deepseek" />
<VectorBlock apiKey="cb_key" onResultsSelect={handleResults} />
<BIMBlock apiKey="cb_key" projectId="project_01" />`}
        </pre>
      </div>
    </div>
  );
}
