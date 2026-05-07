import { useState, useEffect } from 'react';
import { API } from './api/client';
import { 
  // AI Blocks
  ChatBlock, VectorBlock, StorageBlock, QueueBlock,
  PDFBlock, OCRBlock, WebBlock, SearchBlock, ZvecBlock,
  VoiceBlock, ImageBlock, TranslateBlock, CodeBlock,
  BIMBlock, DroneBlock,
  // Drive Blocks
  GoogleDriveBlock, OneDriveBlock, AndroidDriveBlock,
  // Infrastructure Blocks
  FailoverBlock, ConfigBlock, AuthBlock, MemoryBlock, 
  MonitoringBlock, HALBlock,
  // Integration Blocks
  DatabaseBlock, EmailBlock, WebhookBlock, WorkflowBlock, BillingBlock
} from './blocks';

function App() {
  const API_KEY = import.meta.env.VITE_API_KEY || 'cb_dev_key';

  useEffect(() => {
    API.setKey(API_KEY);
  }, []);
  const [activeJob] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<any>(null);
  const [activeTab, setActiveTab] = useState<'ai' | 'infrastructure' | 'integration' | 'storage'>('ai');

  const TabButton = ({ tab, label, icon }: { tab: typeof activeTab; label: string; icon: string }) => (
    <button
      onClick={() => setActiveTab(tab)}
      style={{
        padding: '10px 20px',
        background: activeTab === tab ? '#007bff' : '#e9ecef',
        color: activeTab === tab ? 'white' : '#333',
        border: 'none',
        borderRadius: '4px',
        cursor: 'pointer',
        fontWeight: activeTab === tab ? 'bold' : 'normal',
        display: 'flex',
        alignItems: 'center',
        gap: '8px'
      }}
    >
      {icon} {label}
    </button>
  );

  return (
    <div style={{ padding: '20px', maxWidth: '1400px', margin: '0 auto' }}>
      <h1>🧠 Cerebrum Blocks - Universal AI Platform</h1>
      <p>23+ Blocks. One Platform. Infinite Possibilities.</p>

      {/* System Health */}
      <div style={{ marginBottom: '20px', border: '1px solid #ddd', borderRadius: '8px' }}>
        <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>
          🛡️ System Health & Failover
        </div>
        <FailoverBlock apiKey={API_KEY} />
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', gap: '10px', marginBottom: '20px' }}>
        <TabButton tab="ai" label="AI Blocks" icon="🤖" />
        <TabButton tab="infrastructure" label="Infrastructure" icon="🔧" />
        <TabButton tab="integration" label="Integration" icon="🔌" />
        <TabButton tab="storage" label="Storage" icon="💾" />
      </div>

      {/* AI Blocks Tab */}
      {activeTab === 'ai' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px' }}>
          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>💬 Chat (DeepSeek)</div>
            <ChatBlock apiKey={API_KEY} provider="deepseek" maxHeight="300px" />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🔍 Vector Search</div>
            <VectorBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>📄 PDF</div>
            <PDFBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>👁️ OCR</div>
            <OCRBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🖼️ Image</div>
            <ImageBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🔊 Voice</div>
            <VoiceBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🕸️ Web</div>
            <WebBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🔎 Search</div>
            <SearchBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🌐 Translate</div>
            <TranslateBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>💻 Code</div>
            <CodeBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🧮 Zvec</div>
            <ZvecBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🏗️ BIM</div>
            <BIMBlock apiKey={API_KEY} projectId="demo_project" />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🚁 Drone</div>
            <DroneBlock apiKey={API_KEY} projectId="demo_project" />
          </div>
        </div>
      )}

      {/* Infrastructure Tab */}
      {activeTab === 'infrastructure' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px' }}>
          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🔧 HAL</div>
            <HALBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🧠 Memory</div>
            <MemoryBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>📊 Monitoring</div>
            <MonitoringBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🔐 Auth</div>
            <AuthBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>⚙️ Config</div>
            <ConfigBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>💰 Billing</div>
            <BillingBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>📬 Queue</div>
            <QueueBlock apiKey={API_KEY} jobId={activeJob || undefined} />
          </div>
        </div>
      )}

      {/* Integration Tab */}
      {activeTab === 'integration' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px' }}>
          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🗄️ Database</div>
            <DatabaseBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>📧 Email</div>
            <EmailBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>🎯 Webhook</div>
            <WebhookBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>⚡ Workflow</div>
            <WorkflowBlock apiKey={API_KEY} />
          </div>
        </div>
      )}

      {/* Storage Tab */}
      {activeTab === 'storage' && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '20px' }}>
          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>💾 Storage</div>
            <StorageBlock apiKey={API_KEY} onFileSelect={setSelectedFile} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>📁 Google Drive</div>
            <GoogleDriveBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>☁️ OneDrive</div>
            <OneDriveBlock apiKey={API_KEY} />
          </div>

          <div style={{ border: '1px solid #ddd', borderRadius: '8px' }}>
            <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd' }}>📱 Android Drive</div>
            <AndroidDriveBlock apiKey={API_KEY} />
          </div>
        </div>
      )}

      {/* Selected File Info */}
      {selectedFile && (
        <div style={{ 
          marginTop: '20px', 
          padding: '15px', 
          background: '#e3f2fd', 
          borderRadius: '8px',
          fontSize: '12px'
        }}>
          <strong>Selected File:</strong> {selectedFile.name} ({selectedFile.size} bytes)
        </div>
      )}

      {/* Usage Example */}
      <div style={{ marginTop: '30px', padding: '15px', background: '#f5f5f5', borderRadius: '8px' }}>
        <h4>3 Lines of Code Per Block:</h4>
        <pre style={{ background: '#1e1e1e', color: '#d4d4d4', padding: '15px', borderRadius: '4px', overflow: 'auto' }}>
{`import { ChatBlock, VectorBlock, PDFBlock } from './blocks';

<ChatBlock apiKey="cb_key" provider="deepseek" />
<VectorBlock apiKey="cb_key" onResultsSelect={handleSelect} />
<PDFBlock apiKey="cb_key" onExtract={handleExtract} />`}
        </pre>
      </div>
    </div>
  );
}

export default App;
