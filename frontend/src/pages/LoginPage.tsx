/**
 * LoginPage — standalone (no Shell), two-step login with optional TOTP (D-20, D-23).
 *
 * Step 1: username + password → POST /api/auth/login
 *   - Success (access_token present): store token, redirect to /search (D-25)
 *   - Success (totp_required): show step 2 inputs, store tmp_token in local state
 * Step 2: TOTP code → POST /api/auth/totp/confirm
 *   - Success: store token, redirect to /search
 *
 * Exact copy strings from UI-SPEC (D-24):
 *   - Card heading: "Sign in"
 *   - Card subheading: "smart-search"
 *   - Error wrong creds: "Credenziali non valide. Riprova."
 *   - Error invalid TOTP: "Codice non valido o scaduto. Riprova."
 *   - Error TOTP expired: "Sessione scaduta. Rieffettua il login."
 *   - Error network: "Errore di rete. Verifica la connessione."
 *   - TOTP hint: "Inserisci il codice dell'app di autenticazione"
 */
import { useState, useRef, useEffect } from 'react'
import { useNavigate } from 'react-router'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import LanguageToggle from '@/components/LanguageToggle'

type Step = 'credentials' | 'totp'

export default function LoginPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { setToken } = useAuth()

  const [step, setStep] = useState<Step>('credentials')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [totpCode, setTotpCode] = useState('')
  const [tmpToken, setTmpToken] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const usernameRef = useRef<HTMLInputElement>(null)
  const totpRef = useRef<HTMLInputElement>(null)

  // autoFocus on username at mount (D-23)
  useEffect(() => {
    usernameRef.current?.focus()
  }, [])

  // autoFocus on TOTP input when step switches to totp
  useEffect(() => {
    if (step === 'totp') {
      totpRef.current?.focus()
    }
  }, [step])

  async function handleCredentialsSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await res.json()
      if (!res.ok) {
        setError(t('login.errCreds'))
        return
      }
      if (data.status === 'totp_required') {
        setTmpToken(data.tmp_token)
        setStep('totp')
        return
      }
      setToken(data.access_token)
      navigate('/search', { replace: true })
    } catch {
      setError(t('login.errNetwork'))
    } finally {
      setLoading(false)
    }
  }

  async function handleTotpSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch('/api/auth/totp/confirm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tmp_token: tmpToken, totp_code: totpCode }),
      })
      const data = await res.json()
      if (!res.ok) {
        const detail: string = data.detail ?? ''
        if (detail.includes('Sessione scaduta')) {
          setError(t('login.errSessionExpired'))
          setStep('credentials')
          setTmpToken('')
          setTotpCode('')
        } else {
          setError(t('login.errTotpInvalid'))
        }
        return
      }
      setToken(data.access_token)
      navigate('/search', { replace: true })
    } catch {
      setError(t('login.errNetwork'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-4 bg-background">
      <div className="w-[400px] max-w-[calc(100vw-2rem)] p-6 rounded-lg border bg-card shadow-sm">
        {/* Header */}
        <div className="mb-6 text-center">
          <p className="text-sm text-muted-foreground">smart-search</p>
          <h1 className="text-2xl font-semibold mt-1">{t('login.title')}</h1>
        </div>

        {/* Step 1: credentials */}
        <form onSubmit={handleCredentialsSubmit} className={step === 'totp' ? 'hidden' : ''}>
          <div className="space-y-4">
            <div>
              <label htmlFor="username" className="block text-sm font-medium mb-1">
                {t('login.username')}
              </label>
              <input
                id="username"
                ref={usernameRef}
                type="text"
                value={username}
                onChange={e => setUsername(e.target.value)}
                disabled={step === 'totp' || loading}
                required
                className="w-full h-10 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              />
            </div>
            <div>
              <label htmlFor="password" className="block text-sm font-medium mb-1">
                {t('login.password')}
              </label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                disabled={step === 'totp' || loading}
                required
                className="w-full h-10 rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              />
            </div>
            {error && step === 'credentials' && (
              <p className="text-sm text-destructive">{error}</p>
            )}
            <button
              type="submit"
              disabled={loading}
              className="w-full h-10 rounded-md bg-primary text-primary-foreground text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {loading && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('login.signIn')}
            </button>
          </div>
        </form>

        {/* Step 2: TOTP */}
        {step === 'totp' && (
          <form onSubmit={handleTotpSubmit}>
            <div className="space-y-4">
              <p className="text-sm text-muted-foreground text-center">
                {t('login.totpHint')}
              </p>
              <div>
                <label htmlFor="totp-code" className="block text-sm font-medium mb-1">
                  {t('login.totpLabel')}
                </label>
                <input
                  id="totp-code"
                  ref={totpRef}
                  type="text"
                  inputMode="numeric"
                  maxLength={6}
                  value={totpCode}
                  onChange={e => setTotpCode(e.target.value.replace(/\D/g, ''))}
                  required
                  className="w-full h-11 rounded-md border border-input bg-background px-3 py-2 text-sm text-center tracking-widest focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              {error && (
                <p className="text-sm text-destructive">{error}</p>
              )}
              <button
                type="submit"
                disabled={loading || totpCode.length !== 6}
                className="w-full h-10 rounded-md bg-primary text-primary-foreground text-sm font-medium flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {loading && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('login.confirm')}
              </button>
              <button
                type="button"
                onClick={() => { setStep('credentials'); setError(''); setTotpCode(''); setTmpToken('') }}
                className="w-full text-sm text-muted-foreground underline underline-offset-2"
              >
                {t('login.backToLogin')}
              </button>
            </div>
          </form>
        )}
      </div>
      <LanguageToggle />
    </div>
  )
}
