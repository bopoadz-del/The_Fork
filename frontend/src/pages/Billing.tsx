import { useState } from 'react'
import { CreditCard, Check } from 'lucide-react'

interface Plan {
  id: string
  name: string
  price: number
  description: string
  features: string[]
  current: boolean
  buttonText: string
}

const plans: Plan[] = [
  {
    id: 'free',
    name: 'Free',
    price: 0,
    description: 'For hobby projects and experimentation',
    features: [
      '1,000 requests/month',
      '100,000 tokens/month',
      '9 AI blocks',
      'Community support',
      'Standard latency',
    ],
    current: false,
    buttonText: 'Downgrade',
  },
  {
    id: 'pro',
    name: 'Pro',
    price: 29,
    description: 'For professional developers and teams',
    features: [
      '50,000 requests/month',
      '5,000,000 tokens/month',
      'All 16 AI blocks',
      'Priority support',
      'Streaming responses',
      'Vector search',
      'Lower latency',
    ],
    current: true,
    buttonText: 'Current Plan',
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    price: -1,
    description: 'For large-scale applications',
    features: [
      'Unlimited requests',
      'Unlimited tokens',
      'All 16 AI blocks',
      'Custom blocks',
      'Dedicated support',
      'SLA guarantee',
      'On-premise option',
    ],
    current: false,
    buttonText: 'Contact Sales',
  },
]

interface Invoice {
  id: string
  date: string
  amount: number
  status: 'paid' | 'pending'
  description: string
}

function PlanCard({ plan, onSelect }: { plan: Plan; onSelect: (id: string) => void }) {
  return (
    <div 
      className="card" 
      style={{ 
        borderColor: plan.current ? 'var(--accent-primary)' : undefined,
        position: 'relative'
      }}
    >
      {plan.current && (
        <div style={{
          position: 'absolute',
          top: -12,
          left: '50%',
          transform: 'translateX(-50%)',
          background: 'linear-gradient(135deg, var(--accent-primary), var(--accent-secondary))',
          color: 'white',
          padding: '4px 16px',
          borderRadius: 20,
          fontSize: '0.75rem',
          fontWeight: 600,
        }}>
          Current Plan
        </div>
      )}
      
      <div style={{ textAlign: 'center', marginBottom: 24 }}>
        <h3 style={{ fontSize: '1.5rem', marginBottom: 8 }}>{plan.name}</h3>
        <div style={{ fontSize: '3rem', fontWeight: 800, marginBottom: 8 }}>
          {plan.price === -1 ? 'Custom' : `$${plan.price}`}
          {plan.price !== -1 && <span style={{ fontSize: '1rem', color: 'var(--text-secondary)', fontWeight: 400 }}>/mo</span>}
        </div>
        <p style={{ color: 'var(--text-secondary)' }}>{plan.description}</p>
      </div>

      <ul style={{ listStyle: 'none', marginBottom: 24 }}>
        {plan.features.map((feature, i) => (
          <li key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 0' }}>
            <Check size={18} style={{ color: 'var(--success)', flexShrink: 0 }} />
            <span>{feature}</span>
          </li>
        ))}
      </ul>

      <button 
        className={`btn ${plan.current ? 'btn-secondary' : 'btn-primary'}`}
        style={{ width: '100%', justifyContent: 'center' }}
        onClick={() => onSelect(plan.id)}
        disabled={plan.current}
      >
        {plan.buttonText}
      </button>
    </div>
  )
}

export default function Billing() {
  const [invoices] = useState<Invoice[]>([
    { id: 'inv_001', date: 'Jan 1, 2025', amount: 29.00, status: 'paid', description: 'Pro Plan - January 2025' },
    { id: 'inv_002', date: 'Dec 1, 2024', amount: 29.00, status: 'paid', description: 'Pro Plan - December 2024' },
    { id: 'inv_003', date: 'Nov 1, 2024', amount: 29.00, status: 'paid', description: 'Pro Plan - November 2024' },
  ])

  const handleSelectPlan = (planId: string) => {
    console.log('Selected plan:', planId)
    // Handle plan selection
  }

  return (
    <div>
      <div className="page-header">
        <h1>Billing</h1>
        <p>Manage your subscription and payment methods.</p>
      </div>

      <div className="card" style={{ marginBottom: 32 }}>
        <div className="card-header">
          <h3 className="card-title">Payment Method</h3>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ 
            width: 48, 
            height: 48, 
            background: 'var(--bg-tertiary)', 
            borderRadius: 8,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}>
            <CreditCard size={24} style={{ color: 'var(--accent-primary)' }} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500 }}>•••• •••• •••• 4242</div>
            <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>Expires 12/26</div>
          </div>
          <button className="btn btn-secondary">Update</button>
        </div>
      </div>

      <h2 style={{ marginBottom: 24 }}>Choose Your Plan</h2>
      <div className="grid-3" style={{ marginBottom: 48 }}>
        {plans.map(plan => (
          <PlanCard key={plan.id} plan={plan} onSelect={handleSelectPlan} />
        ))}
      </div>

      <div className="card">
        <div className="card-header">
          <h3 className="card-title">Invoice History</h3>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Invoice</th>
              <th>Date</th>
              <th>Description</th>
              <th>Amount</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {invoices.map(invoice => (
              <tr key={invoice.id}>
                <td style={{ fontFamily: 'monospace', fontSize: '0.875rem' }}>{invoice.id}</td>
                <td>{invoice.date}</td>
                <td>{invoice.description}</td>
                <td>${invoice.amount.toFixed(2)}</td>
                <td>
                  <span className={`badge ${invoice.status === 'paid' ? 'badge-success' : 'badge-warning'}`}>
                    {invoice.status.toUpperCase()}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
