import { useState } from 'react'
import { 
  User, 
  Bell, 
  Shield, 
  Globe, 
  Key,
  Save,
  Check
} from 'lucide-react'

interface SettingsSection {
  id: string
  title: string
  icon: React.ElementType
}

const sections: SettingsSection[] = [
  { id: 'profile', title: 'Profile', icon: User },
  { id: 'notifications', title: 'Notifications', icon: Bell },
  { id: 'security', title: 'Security', icon: Shield },
  { id: 'api', title: 'API Settings', icon: Key },
  { id: 'general', title: 'General', icon: Globe },
]

function ProfileSettings() {
  const [saved, setSaved] = useState(false)
  const [formData, setFormData] = useState({
    name: 'Chadi Mahmoud',
    email: 'chadi@example.com',
    company: 'Cerebrum Blocks',
    role: 'Developer'
  })

  const handleSave = () => {
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div>
      <h3 style={{ marginBottom: 24 }}>Profile Settings</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Full Name</label>
          <input 
            type="text" 
            className="input" 
            value={formData.name}
            onChange={e => setFormData({...formData, name: e.target.value})}
          />
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Email</label>
          <input 
            type="email" 
            className="input" 
            value={formData.email}
            onChange={e => setFormData({...formData, email: e.target.value})}
          />
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Company</label>
          <input 
            type="text" 
            className="input" 
            value={formData.company}
            onChange={e => setFormData({...formData, company: e.target.value})}
          />
        </div>
        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Role</label>
          <select 
            className="input" 
            value={formData.role}
            onChange={e => setFormData({...formData, role: e.target.value})}
          >
            <option>Developer</option>
            <option>Product Manager</option>
            <option>Data Scientist</option>
            <option>Other</option>
          </select>
        </div>
        <button 
          className="btn btn-primary" 
          onClick={handleSave}
          style={{ alignSelf: 'flex-start', marginTop: 12 }}
        >
          {saved ? <><Check size={18} /> Saved</> : <><Save size={18} /> Save Changes</>}
        </button>
      </div>
    </div>
  )
}

function NotificationSettings() {
  const [settings, setSettings] = useState({
    emailAlerts: true,
    usageAlerts: true,
    securityAlerts: true,
    newsletter: false,
    productUpdates: true
  })

  const toggleSetting = (key: keyof typeof settings) => {
    setSettings({ ...settings, [key]: !settings[key] })
  }

  return (
    <div>
      <h3 style={{ marginBottom: 24 }}>Notification Preferences</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {[
          { key: 'emailAlerts', label: 'Email Alerts', description: 'Receive important account notifications via email' },
          { key: 'usageAlerts', label: 'Usage Alerts', description: 'Get notified when you reach 75%, 90%, and 100% of your limits' },
          { key: 'securityAlerts', label: 'Security Alerts', description: 'Notifications for suspicious login attempts and security events' },
          { key: 'productUpdates', label: 'Product Updates', description: 'New features, improvements, and API changes' },
          { key: 'newsletter', label: 'Newsletter', description: 'Monthly newsletter with AI tips and best practices' },
        ].map((item) => (
          <div key={item.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '16px 0', borderBottom: '1px solid var(--border)' }}>
            <div>
              <div style={{ fontWeight: 500 }}>{item.label}</div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>{item.description}</div>
            </div>
            <button
              onClick={() => toggleSetting(item.key as keyof typeof settings)}
              style={{
                width: 48,
                height: 26,
                borderRadius: 13,
                border: 'none',
                background: settings[item.key as keyof typeof settings] ? 'var(--accent-primary)' : 'var(--bg-tertiary)',
                position: 'relative',
                cursor: 'pointer',
                transition: 'all 0.2s'
              }}
            >
              <div style={{
                width: 20,
                height: 20,
                borderRadius: '50%',
                background: 'white',
                position: 'absolute',
                top: 3,
                left: settings[item.key as keyof typeof settings] ? 25 : 3,
                transition: 'all 0.2s'
              }} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function SecuritySettings() {
  return (
    <div>
      <h3 style={{ marginBottom: 24 }}>Security Settings</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div className="card" style={{ background: 'var(--bg-tertiary)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>Two-Factor Authentication</div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>Add an extra layer of security to your account</div>
            </div>
            <button className="btn btn-secondary">Enable</button>
          </div>
        </div>

        <div className="card" style={{ background: 'var(--bg-tertiary)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>Change Password</div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>Last changed 3 months ago</div>
            </div>
            <button className="btn btn-secondary">Update</button>
          </div>
        </div>

        <div className="card" style={{ background: 'var(--bg-tertiary)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <div style={{ fontWeight: 500, marginBottom: 4 }}>Active Sessions</div>
              <div style={{ fontSize: '0.875rem', color: 'var(--text-secondary)' }}>3 active sessions across 2 devices</div>
            </div>
            <button className="btn btn-secondary" style={{ color: 'var(--error)' }}>Manage</button>
          </div>
        </div>

        <div>
          <h4 style={{ marginBottom: 16 }}>Login History</h4>
          <table className="table">
            <thead>
              <tr>
                <th>Device</th>
                <th>Location</th>
                <th>IP Address</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Chrome on MacOS</td>
                <td>San Francisco, CA</td>
                <td>192.168.1.***</td>
                <td>Just now</td>
              </tr>
              <tr>
                <td>Safari on iPhone</td>
                <td>San Francisco, CA</td>
                <td>192.168.1.***</td>
                <td>2 hours ago</td>
              </tr>
              <tr>
                <td>Firefox on Windows</td>
                <td>New York, NY</td>
                <td>10.0.0.***</td>
                <td>1 day ago</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function APISettings() {
  const [webhookUrl, setWebhookUrl] = useState('')
  const [rateLimit, setRateLimit] = useState('100')

  return (
    <div>
      <h3 style={{ marginBottom: 24 }}>API Settings</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Webhook URL</label>
          <input 
            type="text" 
            className="input" 
            placeholder="https://your-app.com/webhook"
            value={webhookUrl}
            onChange={e => setWebhookUrl(e.target.value)}
          />
          <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: 8 }}>
            We&apos;ll send event notifications to this URL
          </div>
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Rate Limit (requests/minute)</label>
          <input 
            type="number" 
            className="input" 
            value={rateLimit}
            onChange={e => setRateLimit(e.target.value)}
          />
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Default Timeout (seconds)</label>
          <input 
            type="number" 
            className="input" 
            defaultValue={30}
          />
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Retry Policy</label>
          <select className="input">
            <option>No retries</option>
            <option>Retry once</option>
            <option>Retry up to 3 times</option>
            <option>Exponential backoff</option>
          </select>
        </div>

        <button className="btn btn-primary" style={{ alignSelf: 'flex-start' }}>
          <Save size={18} /> Save API Settings
        </button>
      </div>
    </div>
  )
}

function GeneralSettings() {
  return (
    <div>
      <h3 style={{ marginBottom: 24 }}>General Settings</h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Timezone</label>
          <select className="input">
            <option>UTC (Coordinated Universal Time)</option>
            <option>America/New_York (Eastern Time)</option>
            <option>America/Chicago (Central Time)</option>
            <option>America/Denver (Mountain Time)</option>
            <option>America/Los_Angeles (Pacific Time)</option>
            <option>Europe/London (GMT)</option>
            <option>Europe/Paris (CET)</option>
            <option>Asia/Tokyo (JST)</option>
          </select>
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Date Format</label>
          <select className="input">
            <option>MM/DD/YYYY</option>
            <option>DD/MM/YYYY</option>
            <option>YYYY-MM-DD</option>
          </select>
        </div>

        <div>
          <label style={{ display: 'block', marginBottom: 8, fontSize: '0.875rem' }}>Language</label>
          <select className="input">
            <option>English (US)</option>
            <option>English (UK)</option>
            <option>Spanish</option>
            <option>French</option>
            <option>German</option>
            <option>Chinese (Simplified)</option>
            <option>Japanese</option>
          </select>
        </div>

        <button className="btn btn-primary" style={{ alignSelf: 'flex-start' }}>
          <Save size={18} /> Save Preferences
        </button>
      </div>
    </div>
  )
}

export default function Settings() {
  const [activeSection, setActiveSection] = useState('profile')

  const renderSection = () => {
    switch (activeSection) {
      case 'profile': return <ProfileSettings />
      case 'notifications': return <NotificationSettings />
      case 'security': return <SecuritySettings />
      case 'api': return <APISettings />
      case 'general': return <GeneralSettings />
      default: return <ProfileSettings />
    }
  }

  return (
    <div>
      <div className="page-header">
        <h1>Settings</h1>
        <p>Manage your account settings and preferences.</p>
      </div>

      <div style={{ display: 'flex', gap: 24 }}>
        <div style={{ width: 240, flexShrink: 0 }}>
          <div className="card" style={{ padding: 8 }}>
            {sections.map(section => {
              const Icon = section.icon
              const isActive = activeSection === section.id
              return (
                <button
                  key={section.id}
                  onClick={() => setActiveSection(section.id)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    width: '100%',
                    padding: '12px 16px',
                    border: 'none',
                    borderRadius: 8,
                    background: isActive ? 'var(--accent-primary)' : 'transparent',
                    color: isActive ? 'white' : 'var(--text-primary)',
                    cursor: 'pointer',
                    fontSize: '0.875rem',
                    fontWeight: isActive ? 500 : 400,
                    marginBottom: 4,
                    transition: 'all 0.15s'
                  }}
                >
                  <Icon size={18} />
                  {section.title}
                </button>
              )
            })}
          </div>
        </div>

        <div style={{ flex: 1 }}>
          <div className="card">
            {renderSection()}
          </div>
        </div>
      </div>
    </div>
  )
}
