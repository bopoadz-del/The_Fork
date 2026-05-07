// Drone-UI-Block - Drone footage upload and analysis
// <DroneBlock apiKey="cb_key" projectId="project_01" />

import { useState } from 'react';

interface DroneBlockProps {
  apiKey: string;
  projectId: string;
  onDefectFound?: (defect: any) => void;
}

export const DroneBlock: React.FC<DroneBlockProps> = ({ 
  apiKey, 
  projectId,
  onDefectFound
}) => {
  const [videoPath, setVideoPath] = useState('');
  const [flightDate, setFlightDate] = useState(new Date().toISOString().split('T')[0]);
  const [loading, setLoading] = useState(false);
  const [defects, setDefects] = useState<any[]>([]);
  const [progress, setProgress] = useState<any>(null);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const processVideo = async () => {
    if (!videoPath) return;
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/drone/process`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ 
          action: 'process_video',
          project_id: projectId,
          video_path: videoPath,
          flight_date: flightDate,
          area_polygons: [],
          altitude: 80
        })
      });

      const data = await response.json();
      console.log('Processed:', data);
    } catch (error) {
      console.error('Drone process failed:', error);
    } finally {
      setLoading(false);
    }
  };

  const detectDefects = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/drone/defects`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ 
          action: 'detect_defects',
          image_paths: [`flights/${flightDate}/frame_001.jpg`],
          types: ['concrete_crack', 'masonry_alignment']
        })
      });

      const data = await response.json();
      setDefects(data.defects || []);
      data.defects?.forEach((d: any) => onDefectFound?.(d));
    } catch (error) {
      console.error('Defect detection failed:', error);
    } finally {
      setLoading(false);
    }
  };

  const checkProgress = async () => {
    try {
      const response = await fetch(`${API_BASE}/v1/drone/progress`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({ 
          action: 'compare_to_bim',
          project_id: projectId,
          flight_date: flightDate
        })
      });

      const data = await response.json();
      setProgress(data);
    } catch (error) {
      console.error('Progress check failed:', error);
    }
  };

  return (
    <div className="drone-block" style={{ padding: '10px', border: '1px solid #ddd', borderRadius: '4px' }}>
      <h4>🚁 Drone Vision</h4>
      
      <div style={{ marginBottom: '10px' }}>
        <input
          type="text"
          value={videoPath}
          onChange={(e) => setVideoPath(e.target.value)}
          placeholder="Video path (e.g., flights/flight_001.mp4)"
          style={{ width: '100%', padding: '8px', marginBottom: '5px' }}
        />
        <input
          type="date"
          value={flightDate}
          onChange={(e) => setFlightDate(e.target.value)}
          style={{ padding: '8px' }}
        />
      </div>

      <div style={{ display: 'flex', gap: '10px', marginBottom: '15px' }}>
        <button onClick={processVideo} disabled={loading} style={{ padding: '8px 16px' }}>
          🎥 Process
        </button>
        <button onClick={detectDefects} disabled={loading} style={{ padding: '8px 16px' }}>
          🔍 Detect Defects
        </button>
        <button onClick={checkProgress} style={{ padding: '8px 16px' }}>
          📊 Progress
        </button>
      </div>

      {progress && (
        <div style={{ padding: '10px', background: '#e8f5e9', marginBottom: '10px', borderRadius: '4px' }}>
          <div>Construction Progress: {progress.progress_percent}%</div>
          <div>BIM Elements: {progress.bim_elements_total} | Detected: {progress.visually_detected}</div>
        </div>
      )}

      {defects.length > 0 && (
        <div className="defects-list">
          <h5>⚠️ Defects Found ({defects.length})</h5>
          {defects.map((defect, idx) => (
            <div 
              key={idx}
              onClick={() => onDefectFound?.(defect)}
              style={{ 
                padding: '8px', 
                marginBottom: '5px',
                background: defect.severity === 'high' ? '#ffebee' : '#fff3e0',
                borderRadius: '4px',
                cursor: 'pointer'
              }}
            >
              <strong>{defect.type}</strong> - {defect.severity} severity
              <div style={{ fontSize: '12px', color: '#666' }}>
                Confidence: {(defect.confidence * 100).toFixed(1)}%
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
