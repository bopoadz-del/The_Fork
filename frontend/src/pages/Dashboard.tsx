import { useState } from 'react'
import { Activity, Zap, CreditCard, TrendingUp } from 'lucide-react'

interface Stats {
  totalRequests: number
  totalTokens: number
  avgLatency: number
  successRate: number
}

function StatCard({ 
  label, 
  value, 
  change, 
  icon: Icon 
}: { 
  label: string
  value: string
  change?: { value: string; positive: boolean }
  icon: React.ElementType
}) {
  return (
    <div className="stat-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div className="stat-label">{label}</div>
          <div className="stat-value">{value}</div>
          {change && (
            <div className={`stat-change ${change.positive ? 'positive' : 'negative'}`}>
              {change.positive ? '↑' : '↓'} {change.value} from last month
            </div>
          )}
        </div>
        <div style={{ 
          padding: 12, 
          background: 'var(--bg-tertiary)', 
          borderRadius: 12,
          color: 'var(--accent-primary)'
        }}>
          <Icon size={24} />
        </div>
      </div>
    </div>
  )
}

function RecentActivity() {
  const activities = [
    { action: 'API Key Created', time: '2 hours ago', detail: 'Production Key' },
    { action: 'Usage Alert', time: '5 hours ago', detail: '75% of monthly limit' },
    { action: 'Payment Processed', time: '1 day ago', detail: '$29.00 - Pro Plan' },
    { action: 'New Block Used', time: '2 days ago', detail: 'Vector Search' },
  ]

  return (
    <div className="card">
      <div className="card-header">
        <h3 className="card-title">Recent Activity</h3>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {activities.map((activity, i) => (
          <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{activity.action}</div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>{activity.detail}</div>
            </div>
            <span style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>{activity.time}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function QuickActions() {
  return (
    <div className="card">
      <div className="card-header">
        <h3 className="card-title">Quick Actions</h3>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <button className="btn btn-primary" style={{ justifyContent: 'center' }}>
          Create New API Key
        </button>
        <button className="btn btn-secondary" style={{ justifyContent: 'center' }}>
          View Documentation
        </button>
        <button className="btn btn-secondary" style={{ justifyContent: 'center' }}>
          Upgrade Plan
        </button>
      </div>
    </div>
  )
}

function UsageChart() {
  // Mock data - in real app, fetch from API
  const data = [
    { day: 'Mon', requests: 120 },
    { day: 'Tue', requests: 150 },
    { day: 'Wed', requests: 180 },
    { day: 'Thu', requests: 140 },
    { day: 'Fri', requests: 200 },
    { day: 'Sat', requests: 90 },
    { day: 'Sun', requests: 80 },
  ]

  const maxRequests = Math.max(...data.map(d => d.requests))

  return (
    <div className="card">
      <div className="card-header">
        <h3 className="card-title">Request Volume (7 days)</h3>
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 12, height: 200, paddingTop: 20 }}>
        {data.map((d, i) => (
          <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <div 
              style={{ 
                width: '100%', 
                height: `${(d.requests / maxRequests) * 160}px`,
                background: 'linear-gradient(180deg, var(--accent-primary), var(--accent-secondary))',
                borderRadius: '4px 4px 0 0',
                minHeight: 4
              }} 
            />
            <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{d.day}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [stats] = useState<Stats>({
    totalRequests: 12847,
    totalTokens: 2843920,
    avgLatency: 245,
    successRate: 99.8
  })

  return (
    <div>
      <div className="page-header">
        <h1>Dashboard</h1>
        <p>Welcome back! Here's what's happening with your API usage.</p>
      </div>

      <div className="stats-grid">
        <StatCard 
          label="Total Requests" 
          value={stats.totalRequests.toLocaleString()}
          change={{ value: '12%', positive: true }}
          icon={Activity}
        />
        <StatCard 
          label="Tokens Used" 
          value={stats.totalTokens.toLocaleString()}
          change={{ value: '8%', positive: true }}
          icon={Zap}
        />
        <StatCard 
          label="Avg Latency" 
          value={`${stats.avgLatency}ms`}
          change={{ value: '5%', positive: true }}
          icon={TrendingUp}
        />
        <StatCard 
          label="Success Rate" 
          value={`${stats.successRate}%`}
          icon={CreditCard}
        />
      </div>

      <div className="grid-2">
        <UsageChart />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
          <QuickActions />
          <RecentActivity />
        </div>
      </div>
    </div>
  )
}
