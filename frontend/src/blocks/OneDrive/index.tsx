// OneDrive-UI-Block
export const OneDriveBlock: React.FC<{ apiKey: string; onFileSelect?: (f: any) => void }> = ({ onFileSelect }) => {
  const files = [{ name: 'report.docx' }, { name: 'data.xlsx' }];
  return (
    <div style={{ padding: '10px', border: '1px solid #ddd', borderRadius: '4px' }}>
      <div style={{ padding: '10px', background: '#f5f5f5', borderBottom: '1px solid #ddd', marginBottom: '10px' }}>☁️ OneDrive</div>
      {files.map((f, i) => <div key={i} onClick={() => onFileSelect?.(f)} style={{ padding: '8px', cursor: 'pointer' }}>📄 {f.name}</div>)}
    </div>
  );
};
