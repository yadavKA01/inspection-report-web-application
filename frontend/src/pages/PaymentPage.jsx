import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getUser } from '../auth.js'
import { paymentAPI } from '../services/api.js'

// ── Load Razorpay checkout.js on demand ───────────────────────────────────
function loadRazorpayScript() {
  return new Promise((resolve) => {
    if (window.Razorpay) return resolve(true)
    const script = document.createElement('script')
    script.src = 'https://checkout.razorpay.com/v1/checkout.js'
    script.onload  = () => resolve(true)
    script.onerror = () => resolve(false)
    document.body.appendChild(script)
  })
}

// ── Styles ─────────────────────────────────────────────────────────────────
const S = {
  shell: {
    minHeight: '100vh',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: 'linear-gradient(135deg, #0a1628 0%, #0d1f3c 100%)',
    padding: '2rem',
  },
  card: {
    background: '#111d2e',
    border: '1px solid #1e2d40',
    borderRadius: '16px',
    padding: '2.5rem',
    maxWidth: '480px',
    width: '100%',
    textAlign: 'center',
    boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
  },
  lockIcon: {
    fontSize: '3.5rem',
    marginBottom: '1rem',
    display: 'block',
  },
  title: {
    fontSize: '1.6rem',
    fontWeight: 700,
    color: '#f0f4ff',
    marginBottom: '0.5rem',
  },
  subtitle: {
    color: '#8899aa',
    marginBottom: '2rem',
    lineHeight: 1.6,
    fontSize: '0.95rem',
  },
  planCard: {
    background: '#0d1a2e',
    border: '1px solid #2d4a6e',
    borderRadius: '12px',
    padding: '1.25rem',
    marginBottom: '1.5rem',
  },
  planName: {
    color: '#3b82f6',
    fontWeight: 700,
    fontSize: '1.1rem',
    marginBottom: '0.25rem',
  },
  planPrice: {
    color: '#f0f4ff',
    fontSize: '2rem',
    fontWeight: 800,
    marginBottom: '0.25rem',
  },
  planSub: {
    color: '#8899aa',
    fontSize: '0.82rem',
  },
  features: {
    listStyle: 'none',
    padding: 0,
    margin: '1rem 0 1.5rem',
    textAlign: 'left',
  },
  featureItem: {
    color: '#c8d8e8',
    fontSize: '0.9rem',
    padding: '0.3rem 0',
    display: 'flex',
    alignItems: 'center',
    gap: '0.5rem',
  },
  btn: {
    width: '100%',
    padding: '0.875rem',
    borderRadius: '10px',
    border: 'none',
    fontSize: '1rem',
    fontWeight: 600,
    cursor: 'pointer',
    marginBottom: '0.75rem',
    transition: 'opacity 0.15s',
  },
  btnPrimary: {
    background: 'linear-gradient(135deg, #1d4ed8, #2563eb)',
    color: '#fff',
  },
  btnDisabled: {
    opacity: 0.5,
    cursor: 'not-allowed',
  },
  err: {
    background: 'rgba(239,68,68,0.12)',
    border: '1px solid rgba(239,68,68,0.3)',
    borderRadius: '8px',
    color: '#fca5a5',
    padding: '0.75rem',
    marginBottom: '1rem',
    fontSize: '0.875rem',
  },
  success: {
    background: 'rgba(34,197,94,0.12)',
    border: '1px solid rgba(34,197,94,0.3)',
    borderRadius: '8px',
    color: '#86efac',
    padding: '0.75rem',
    marginBottom: '1rem',
    fontSize: '0.875rem',
  },
  back: {
    marginTop: '1rem',
    color: '#4a6a8a',
    fontSize: '0.82rem',
    cursor: 'pointer',
    background: 'none',
    border: 'none',
    textDecoration: 'underline',
  },
}

const PLAN_FEATURES = [
  'Unlimited drawing uploads',
  'Excel export for all sessions',
  'Session history & management',
  'Priority support',
]

export default function PaymentPage() {
  const navigate  = useNavigate()
  const user      = getUser()
  const [loading, setLoading]   = useState(false)
  const [err,     setErr]       = useState('')
  const [success, setSuccess]   = useState(false)

  async function handlePayRazorpay() {
    setLoading(true)
    setErr('')

    // 1. Load Razorpay checkout script
    const loaded = await loadRazorpayScript()
    if (!loaded) {
      setErr('Could not load payment gateway. Please check your internet connection.')
      setLoading(false)
      return
    }

    // 2. Create order on backend
    const { data, error } = await paymentAPI.createOrder()
    if (error) {
      setErr(error)
      setLoading(false)
      return
    }

    // 3. Open Razorpay checkout
    const options = {
      key:         data.key,
      amount:      data.amount,
      currency:    data.currency,
      name:        'SmorX.ai',
      description: 'Auto Ballooning — Professional Plan',
      order_id:    data.order_id,
      prefill:     { email: user?.email || '' },
      theme:       { color: '#2563eb' },
      handler: async function (response) {
        // 4. Verify payment on backend
        const { data: vData, error: vErr } = await paymentAPI.verify({
          razorpay_order_id:   response.razorpay_order_id,
          razorpay_payment_id: response.razorpay_payment_id,
          razorpay_signature:  response.razorpay_signature,
        })
        if (vErr) {
          setErr('Payment verification failed: ' + vErr)
          setLoading(false)
          return
        }
        setSuccess(true)
        // 5. Redirect to dashboard
        setTimeout(() => navigate('/dashboard'), 2500)
      },
      modal: {
        ondismiss: () => setLoading(false),
      },
    }

    const rzp = new window.Razorpay(options)
    rzp.on('payment.failed', function (response) {
      setErr('Payment failed: ' + (response.error?.description || 'Please try again.'))
      setLoading(false)
    })
    rzp.open()
  }

  // ── Success state ──────────────────────────────────────────────────────
  if (success) {
    return (
      <div style={S.shell}>
        <div style={S.card}>
          <div style={S.title}>Payment Successful!</div>
          <p style={S.subtitle}>
            Your SmorX.ai subscription is now active.<br />
            Redirecting you to the dashboard…
          </p>
          <div style={S.success}>Subscription activated. Welcome aboard!</div>
        </div>
      </div>
    )
  }

  // ── Main payment UI ───────────────────────────────────────────────────
  return (
    <div style={S.shell}>
      <div style={S.card}>
        <div style={S.title}>Your free trial has expired</div>
        <p style={S.subtitle}>
          Upgrade to SmorX.ai Professional to continue using Auto Ballooning.
        </p>

        {/* Plan card */}
        <div style={S.planCard}>
          <div style={S.planName}>Professional Plan</div>
          <div style={S.planPrice}>₹999<span style={{ fontSize: '1rem', fontWeight: 400, color: '#8899aa' }}> / month</span></div>
          <div style={S.planSub}>All features included</div>
        </div>

        {/* Feature list */}
        <ul style={S.features}>
          {PLAN_FEATURES.map(f => (
            <li key={f} style={S.featureItem}>
              {f}
            </li>
          ))}
        </ul>

        {/* Error */}
        {err && <div style={S.err}>{err}</div>}

        {/* Pay button */}
        <button
          style={{ ...S.btn, ...S.btnPrimary, ...(loading ? S.btnDisabled : {}) }}
          onClick={handlePayRazorpay}
          disabled={loading}
        >
          {loading ? 'Opening payment…' : 'Pay ₹999 via Razorpay'}
        </button>

        <p style={{ color: '#4a6a8a', fontSize: '0.78rem', margin: '0.5rem 0 1rem' }}>
          Secured by Razorpay · UPI, Cards, Net Banking accepted
        </p>

        <button style={S.back} onClick={() => navigate('/dashboard')}>
          ← Back to dashboard
        </button>
      </div>
    </div>
  )
}
