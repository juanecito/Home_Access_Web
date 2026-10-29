import { useState } from 'react';
import { useAuth } from '../AuthContext.jsx';
import PasswordInput from '../PasswordInput.jsx';

export default function Login() {
  const { login, register, methods } = useAuth();
  const [mode, setMode] = useState('login');
  const [method, setMethod] = useState('local');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (mode === 'login') await login(username, password, method);
      else await register(username, password);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const isLogin = mode === 'login';
  return (
    <form className="card narrow" onSubmit={submit}>
      <h1>{isLogin ? 'Sign in' : 'Create account'}</h1>
      {isLogin && methods.pam && (
        <div className="segmented" role="radiogroup" aria-label="Account type">
          <button type="button" role="radio" aria-checked={method === 'local'}
                  className={method === 'local' ? 'on' : ''} onClick={() => setMethod('local')}>HomeAcces account</button>
          <button type="button" role="radio" aria-checked={method === 'pam'}
                  className={method === 'pam' ? 'on' : ''} onClick={() => setMethod('pam')}>System account</button>
        </div>
      )}
      {isLogin && method === 'pam' && (
        <p className="muted">Sign in with your Raspberry Pi user (PAM). This enables the web terminal.</p>
      )}
      <label>
        Username
        <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username"
               autoCapitalize="none" required />
      </label>
      <label>
        Password
        <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)}
               autoComplete={isLogin ? 'current-password' : 'new-password'} minLength={isLogin ? 1 : 8} required />
      </label>
      {error && <p className="error">{error}</p>}
      <button type="submit" disabled={busy}>{isLogin ? 'Sign in' : 'Register'}</button>
      {methods.registration && (
        <p className="muted">
          {isLogin ? 'No account?' : 'Already registered?'}{' '}
          <button type="button" className="link"
                  onClick={() => { setMode(isLogin ? 'register' : 'login'); setMethod('local'); setError(''); }}>
            {isLogin ? 'Register' : 'Sign in'}
          </button>
        </p>
      )}
    </form>
  );
}
