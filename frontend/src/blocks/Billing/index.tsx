// Billing-UI-Block - Usage Tracking & Billing
// import { BillingBlock } from './blocks/Billing'
// <BillingBlock apiKey="cb_key" />

import { useState, useEffect } from 'react';

interface BillingBlockProps {
  apiKey: string;
}

interface UsageStats {
  requests_today: number;
  requests_this_month: number;
  tokens_used: number;
  estimated_cost: number;
  quota: {
    limit: number;
    used: number;
    remaining: number;
  };
}

interface BlockUsage {
  block: string;
  requests: number;
  tokens: number;
}

export const BillingBlock: React.FC<BillingBlockProps> = ({ apiKey }) => {
  const [stats, setStats] = useState<UsageStats | null>(null);
  const [blockUsage, setBlockUsage] = useState<BlockUsage[]>([]);
  const [loading, setLoading] = useState(false);

  const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

  const fetchBillingData = async () => {
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/v1/execute`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          block: 'billing',
          action: 'get_usage'
        })
      });
      const data = await response.json();
      setStats(data.usage || null);
      setBlockUsage(data.by_block || []);
    } catch (error) {
      console.error('Failed to fetch billing data');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchBillingData();
  }, [apiKey]);

  const getUsagePercent = () => {
    if (!stats) return 0;
    return Math.min((stats.quota.used / stats.quota.limit) * 100, 100);
  };

  const getUsageColor = () => {
    const percent = getUsagePercent();
    if (percent < 50) return '#28a745';
    if (percent < 80) return '#ffc107';
    return '#dc3545';
  };

  return (
    <div style={{ padding: '15px' }}>
      {/* Refresh */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '15px' }}>
        <h4 style={{ margin: 0 }}>Usage & Billing</h4>
        <button
          onClick={fetchBillingData}
          disabled={loading}
          style={{
            padding: '4px 12px',
            background: '#6c757d',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: loading ? 'not-allowed' : 'pointer',
            fontSize: '11px'
          }}
        >
          {loading ? '...' : 'Refresh'}
        </button>
      </div>

      {stats ? (
        <>
          {/* Quota Progress */}
          <div style={{ marginBottom: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '5px' }}>
              <span>Monthly Quota</span>
              <span>{stats.quota.used.toLocaleString()} / {stats.quota.limit.toLocaleString()}</span>
            </div>
            <div style={{ height: '8px', background: '#e9ecef', borderRadius: '4px', overflow: 'hidden' }}>
              <div style={{
                height: '100%',
                width: `${getUsagePercent()}%`,
                background: getUsageColor(),
                borderRadius: '4px',
                transition: 'width 0.3s'
              }} />
            </div>
            <div style={{ fontSize: '11px', color: '#666', marginTop: '5px' }}>
              {stats.quota.remaining.toLocaleString()} requests remaining
            </div>
          </div>

          {/* Stats Grid */}
          <div style={{ 
            display: 'grid', 
            gridTemplateColumns: 'repeat(2, 1fr)', 
            gap: '10px',
            marginBottom: '20px'
          }}>
            <div style={{ padding: '12px', background: '#e3f2fd', borderRadius: '4px', textAlign: 'center' }}>
              <div style={{ fontSize: '20px', fontWeight: 'bold', color: '#1976d2' }}>
                {stats.requests_today.toLocaleString()}
              </div>
              <div style={{ fontSize: '11px', color: '#666' }}>Requests Today</div>
            </div>
            <div style={{ padding: '12px', background: '#f3e5f5', borderRadius: '4px', textAlign: 'center' }}>
              <div style={{ fontSize: '20px', fontWeight: 'bold', color: '#7b1fa2' }}>
                {stats.requests_this_month.toLocaleString()}
              </div>
              <div style={{ fontSize: '11px', color: '#666' }}>This Month</div>
            </div>
            <div style={{ padding: '12px', background: '#e8f5e9', borderRadius: '4px', textAlign: 'center' }}>
              <div style={{ fontSize: '20px', fontWeight: 'bold', color: '#388e3c' }}>
                {stats.tokens_used.toLocaleString()}
              </div>
              <div style={{ fontSize: '11px', color: '#666' }}>Tokens Used</div>
            </div>
            <div style={{ padding: '12px', background: '#fff3e0', borderRadius: '4px', textAlign: 'center' }}>
              <div style={{ fontSize: '20px', fontWeight: 'bold', color: '#f57c00' }}>
                ${stats.estimated_cost.toFixed(2)}
              </div>
              <div style={{ fontSize: '11px', color: '#666' }}>Est. Cost</div>
            </div>
          </div>

          {/* Block Usage */}
          <div>
            <h5 style={{ margin: '0 0 10px 0' }}>Usage by Block</h5>
            <div style={{ maxHeight: '150px', overflow: 'auto' }}>
              {blockUsage.length === 0 ? (
                <p style={{ color: '#6c757d', fontStyle: 'italic' }}>No block usage data</p>
              ) : (
                blockUsage.map((item, idx) => (
                  <div key={idx} style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '8px',
                    borderBottom: '1px solid #eee',
                    background: idx % 2 === 0 ? '#f8f9fa' : 'white',
                    fontSize: '12px'
                  }}>
                    <span style={{ textTransform: 'capitalize' }}>{item.block}</span>
                    <div style={{ display: 'flex', gap: '15px', color: '#666' }}>
                      <span>{item.requests.toLocaleString()} req</span>
                      <span>{item.tokens.toLocaleString()} tokens</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      ) : (
        <div style={{ textAlign: 'center', padding: '30px', color: '#6c757d' }}>
          {loading ? 'Loading...' : 'No billing data available'}
        </div>
      )}

      {/* Pricing Info */}
      <div style={{ 
        marginTop: '15px', 
        padding: '10px', 
        background: '#e9ecef', 
        borderRadius: '4px',
        fontSize: '11px'
      }}>
        <strong>Pricing:</strong> Free: 1,000/mo • Pro: $29/mo (50K requests) • Enterprise: Custom
      </div>
    </div>
  );
};
