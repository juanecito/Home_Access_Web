import { useEffect, useState } from 'react';
import { api } from './api.js';
import { useAuth } from './AuthContext.jsx';
import PasswordInput from './PasswordInput.jsx';

const EXPIRY_OPTIONS = [
  { label: '7 days', days: 7 },
  { label: '30 days', days: 30 },
  { label: '90 days', days: 90 },
  { label: '1 year', days: 365 },
  { label: 'Never', days: null },
];
const DEFAULT_EXPIRY = '90';

function formatDate(value) {
  return value ? new Date(value).toLocaleDateString() : null;
}

// Personal access tokens: long-lived Bearer credentials for scripts. The secret is shown once.
export default function ApiTokens() {
  const { user } = useAuth();
  const [tokens, setTokens] = useState(null);
  const [maxTokens, setMaxTokens] = useState(null);
  const [name, setName] = useState('');
  const [expiry, setExpiry] = useState(DEFAULT_EXPIRY);
  const [password, setPassword] = useState('');
  const [created, setCreated] = useState(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  async function load() {
    try {
      const data = await api.get('/api/auth/tokens');
      setTokens(data.tokens);
      setMaxTokens(data.max_tokens);
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    }
  }

  useEffect(() => { load(); }, []);

  async function create(e) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const days = expiry === 'never' ? null : Number(expiry);
      const data = await api.post('/api/auth/tokens', { name, expires_in_days: days, password });
      setCreated({ name: data.token.name, secret: data.secret });
      setCopied(false);
      setName(''); setPassword(''); setExpiry(DEFAULT_EXPIRY);
      await load();
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function revoke(token) {
    if (!window.confirm(`Delete the token "${token.name}"? Scripts using it will stop working.`)) return;
    setMessage(null);
    try {
      await api.del(`/api/auth/tokens/${token.id}`);
      if (created && created.name === token.name) setCreated(null);
      setMessage({ ok: true, text: `Token "${token.name}" deleted.` });
      await load();
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(created.secret);
      setCopied(true);
    } catch {
      setMessage({ ok: false, text: 'Could not copy: select the token and copy it by hand.' });
    }
  }

  const full = tokens && maxTokens !== null && tokens.length >= maxTokens;

  return (
    <div className="card tokens-card">
      <h2>Personal access tokens</h2>
      <p className="muted">
        Tokens let scripts call the API as you, with <code>Authorization: Bearer &lt;token&gt;</code>.
        They cannot manage tokens, change your password or open terminals.
        {user.auth_source === 'local' && ' Changing your password deletes all of your tokens.'}
      </p>

      {created && (
        <div className="token-created">
          <p><strong>Token "{created.name}" created.</strong> Copy it now: it will not be shown again.</p>
          <div className="row">
            <input className="grow mono" readOnly value={created.secret} onFocus={(e) => e.target.select()}
                   aria-label="New token" />
            <button type="button" className="secondary" onClick={copy}>{copied ? 'Copied' : 'Copy'}</button>
          </div>
          <pre className="mono">curl -H "Authorization: Bearer {created.secret}" {window.location.origin}/api/items</pre>
          <div className="row end">
            <button type="button" className="secondary" onClick={() => setCreated(null)}>Done</button>
          </div>
        </div>
      )}

      {tokens === null ? (
        <p className="muted">Loading…</p>
      ) : tokens.length === 0 ? (
        <p className="muted">You have no tokens.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Name</th><th>Token</th><th>Expires</th><th>Last used</th><th /></tr>
            </thead>
            <tbody>
              {tokens.map((t) => (
                <tr key={t.id}>
                  <td>{t.name}<div className="muted small">created {formatDate(t.created_at)}</div></td>
                  <td><code>{t.hint}…</code></td>
                  <td className="nowrap">
                    {t.expired
                      ? <span className="badge fail">expired</span>
                      : formatDate(t.expires_at) ?? <span className="muted">never</span>}
                  </td>
                  <td>
                    {t.last_used_at
                      ? <>{new Date(t.last_used_at).toLocaleString()}<div className="muted small">{t.last_used_ip}</div></>
                      : <span className="muted">never</span>}
                  </td>
                  <td className="actions">
                    <button type="button" className="danger" onClick={() => revoke(t)}>Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <form className="token-form" onSubmit={create}>
        <h3>New token</h3>
        <div className="row wrap">
          <label className="grow">
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={64}
                   placeholder="e.g. backup script" required />
          </label>
          <label>
            Expires after
            <select value={expiry} onChange={(e) => setExpiry(e.target.value)}>
              {EXPIRY_OPTIONS.map((o) => (
                <option key={o.label} value={o.days ?? 'never'}>{o.label}</option>
              ))}
            </select>
          </label>
        </div>
        <label>
          {user.auth_source === 'pam' ? 'Your system password' : 'Your password'}
          <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
                         autoComplete="current-password" required />
        </label>
        {message && <p className={message.ok ? 'success' : 'error'}>{message.text}</p>}
        <div className="row end">
          <button type="submit" disabled={busy || full}
                  title={full ? `At most ${maxTokens} tokens: delete one first` : undefined}>
            Create token
          </button>
        </div>
      </form>
    </div>
  );
}
