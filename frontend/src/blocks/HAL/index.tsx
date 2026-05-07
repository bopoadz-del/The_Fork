// HAL-UI-Block - Hardware Abstraction Layer
// import { HALBlock } from './blocks/HAL'
// <HALBlock apiKey="cb_key" />

import { useState, useEffect } from 'react';

interface HALBlockProps {
  apiKey: string;
}

interface HardwareProfile {
  profile: string;
  details: {
    environment: string;
    gpu_available: boolean;
    gpu_name?: string;
    memory_gb?: number;
    platform?: string;
  };
}

const profiles: Record<string, { icon: string; name: string; color: string; desc: string }> = {
  'cloud_render': { icon: '☁️', name: 'Render Cloud', color: '#7c3aed', desc: 'Optimized for Render.com deployment' },
  'cloud_aws': { icon: '📦', name: 'AWS Cloud', color: '#ff9900', desc: 'Amazon Web Services environment' },
  'edge_jetson': { icon: '🔷', name: 'Jetson Edge', color: '#76b900', desc: 'NVIDIA Jetson edge device' },
  'local_gpu': { icon: '🎮', name: 'Local GPU', color: '#00d4aa', desc: 'Local machine with CUDA GPU' },
  'local_std': { icon: '💻', name: 'Standard Local', color: '#6b7280', desc: 'Standard local environment' }
};

export const HALBlock: React.FC<HALBlockProps> = ({ apiKey }) => {
  const [profile, setProfile] = useState<HardwareProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const detectProfile = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${API_BASE}/v1/hal/profile`, {
        headers: { 'Authorization': `Bearer ${apiKey}` }
      });
      if (response.ok) {
        const data = await response.json();
        setProfile(data);
      } else {
        setError('Failed to detect hardware profile');
      }
    } catch (err) {
      setError('Error connecting to HAL service');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    detectProfile();
  }, [apiKey]);

  const profileInfo = profile ? profiles[profile.profile] || profiles['local_std'] : null;

  return (
    <div style={{ padding: '15px' }}>
      {/* Current Profile */}
      <div style={{ 
        padding: '20px', 
        background: profileInfo ? `${profileInfo.color}15` : '#f3f4f6',
        border: `2px solid ${profileInfo?.color || '#d1d5db'}`,
        borderRadius: '8px',
        textAlign: 'center',
        marginBottom: '15px'
      }}>
        {profile && profileInfo ? (
          <>
            <div style={{ fontSize: '48px', marginBottom: '10px' }}>{profileInfo.icon}</div>
            <div style={{ 
              fontSize: '20px', 
              fontWeight: 'bold', 
              color: profileInfo.color,
              marginBottom: '5px'
            }}>
              {profileInfo.name}
            </div>
            <div style={{ fontSize: '12px', color: '#6b7280', marginBottom: '15px' }}>
              {profileInfo.desc}
            </div>
            
            {/* Hardware Details */}
            <div style={{ 
              display: 'grid', 
              gridTemplateColumns: 'repeat(2, 1fr)', 
              gap: '10px',
              textAlign: 'left',
              fontSize: '12px'
            }}>
              <div style={{ padding: '8px', background: 'white', borderRadius: '4px' }}>
                <div style={{ color: '#6b7280' }}>Environment</div>
                <div style={{ fontWeight: 'bold' }}>{profile.details.environment}</div>
              </div>
              <div style={{ padding: '8px', background: 'white', borderRadius: '4px' }}>
                <div style={{ color: '#6b7280' }}>GPU</div>
                <div style={{ fontWeight: 'bold' }}>
                  {profile.details.gpu_available ? (profile.details.gpu_name || 'Available') : 'Not Available'}
                </div>
              </div>
              {profile.details.memory_gb && (
                <div style={{ padding: '8px', background: 'white', borderRadius: '4px' }}>
                  <div style={{ color: '#6b7280' }}>Memory</div>
                  <div style={{ fontWeight: 'bold' }}>{profile.details.memory_gb} GB</div>
                </div>
              )}
              {profile.details.platform && (
                <div style={{ padding: '8px', background: 'white', borderRadius: '4px' }}>
                  <div style={{ color: '#6b7280' }}>Platform</div>
                  <div style={{ fontWeight: 'bold' }}>{profile.details.platform}</div>
                </div>
              )}
            </div>
          </>
        ) : (
          <div style={{ color: '#6b7280' }}>
            {loading ? 'Detecting hardware...' : error || 'No profile detected'}
          </div>
        )}
      </div>

      {/* Detect Button */}
      <button
        onClick={detectProfile}
        disabled={loading}
        style={{
          width: '100%',
          padding: '10px',
          background: '#3b82f6',
          color: 'white',
          border: 'none',
          borderRadius: '4px',
          cursor: loading ? 'not-allowed' : 'pointer',
          fontWeight: 'bold'
        }}
      >
        {loading ? 'Detecting...' : '🔄 Re-detect Hardware'}
      </button>

      {/* Available Profiles */}
      <div style={{ marginTop: '15px' }}>
        <h5 style={{ margin: '0 0 10px 0', fontSize: '12px', color: '#6b7280' }}>Supported Profiles</h5>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
          {Object.entries(profiles).map(([key, info]) => (
            <div key={key} style={{
              padding: '6px 10px',
              borderRadius: '4px',
              fontSize: '11px',
              background: profile?.profile === key ? info.color : '#f3f4f6',
              color: profile?.profile === key ? 'white' : '#374151',
              border: `1px solid ${info.color}`
            }}>
              {info.icon} {info.name}
            </div>
          ))}
        </div>
      </div>

      {/* Info */}
      <div style={{ 
        marginTop: '15px', 
        padding: '10px', 
        background: '#eff6ff', 
        borderRadius: '4px',
        fontSize: '11px',
        color: '#1e40af'
      }}>
        <strong>HAL (Hardware Abstraction Layer)</strong> automatically detects your 
        deployment environment and loads the optimal configuration profile.
      </div>
    </div>
  );
};
