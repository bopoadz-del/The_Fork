import { useState } from 'react'
import { Download } from 'lucide-react'

interface DailyUsage {
  date: string
  requests: number
  tokens: number
}

function UsageBar({ used, limit, label }: { used: number; limit: number; label: string }) {
  const percentage = Math.min((used / limit) * 100, 100)
  
  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <span style={{ fontWeight: 500 }}>{label}</span>
        <span style={{ color: 'var(--text-secondary)' }}>
          {used.toLocaleString()} / {limit.toLocaleString()} ({percentage.toFixed(1)}%)
        </span>
      </div>
      <div className="progress-bar">
        <div 
          className="progress-fill" 
          style={{ 
            width: `${percentage}%`,
            background: percentage > 80 
              ? 'linear-gradient(90deg, var(--warning), var(--error))' 
              : undefined
          }} 
        />
      </div>
    </div>
  )
}

export default function Usage() {
  const [timeRange, setTimeRange] = useState('30d')
  
  // Mock data
  const usage: DailyUsage[] = [
    { date: '2025-01-15', requests: 450, tokens: 89200 },
    { date: '2025-01-14', requests: 380, tokens: 75400 },
    { date: '2025-01-13', requests: 520, tokens: 103200 },
    { date: '2025-01-12', requests: 290, tokens: 57800 },
    { date: '2025-01-11', requests: 410, tokens: 81600 },
    { date: '2025-01-10', requests: 480, tokens: 95400 },
    { date: '2025-01-09', requests: 340, tokens: 67800 },
  ]

  const totalRequests = usage.reduce((sum, d) => sum + d.requests, 0)
  const totalTokens = usage.reduce((sum, d) => sum + d.tokens, 0)

  return (
    <div>
      <div className="page-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div>
            <h1>Usage</h1>
            <p>Track your API usage and monitor your limits.</p>
          </div>
          <div style={{ display: 'flex', gap: 12 }}>
            <select 
              className="input" 
              value={timeRange}
              onChange={e => setTimeRange(e.target.value)}
              style={{ width: 'auto' }}
            >
              <option value="7d">Last 7 days</option>
              <option value="30d">Last 30 days</option>
              <option value="90d">Last 90 days</option>
            </select>
            <button className="btn btn-secondary">
              <Download size={18} />
              Export
            </button>
          </div>
        </div>
      </div>

      <div className="grid-2">
        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Current Plan Limits</h3>
            <span className="badge badge-warning">PRO</span>
          </div>
          <UsageBar used={12847} limit={50000} label="Requests (monthly)" />
          <UsageBar used={2843920} limit={5000000} label="Tokens (monthly)" />
        </div>

        <div className="card">
          <div className="card-header">
            <h3 className="card-title">Summary ({timeRange})</h3>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 24 }}>
            <div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: 4 }}>Total Requests</div>
              <div style={{ fontSize: '2rem', fontWeight: 700 }}>{totalRequests.toLocaleString()}</div>
            </div>
            <div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: 4 }}>Total Tokens</div>
              <div style={{ fontSize: '2rem', fontWeight: 700 }}>{(totalTokens / 1000).toFixed(1)}k</div>
            </div>
            <div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: 4 }}>Avg Latency</div>
              <div style={{ fontSize: '2rem', fontWeight: 700 }}>245ms</div>
            </div>
            <div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: 4 }}>Success Rate</div>
              <div style={{ fontSize: '2rem', fontWeight: 700 }}>99.8%</div>
            </div>
          </div>
        </div>
      </div>

      <div className="card" style={{ marginTop: 24 }}>
        <div className="card-header">
          <h3 className="card-title">Daily Breakdown</h3>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Date</th>
              <th>Requests</th>
              <th>Tokens</th>
              <th>Avg Latency</th>
            </tr>
          </thead>
          <tbody>
            {usage.map((day, i) => (
              <tr key={i}>
                <td>{day.date}</td>
                <td>{day.requests.toLocaleString()}</td>
                <td>{day.tokens.toLocaleString()}</td>
                <td>{Math.floor(Math.random() * 100 + 200)}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
