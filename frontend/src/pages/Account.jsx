import { useState } from 'react';
import { api } from '../api.js';
import { useAuth } from '../AuthContext.jsx';
import AvatarSettings from '../AvatarSettings.jsx';
import PasswordInput from '../PasswordInput.jsx';

export default function Account() {
  const { user, session } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [message, setMessage] = useState(null);

  async function submit(e) {
    e.preventDefault();
    try {
      await api.put('/api/auth/password', { current_password: current, new_password: next });
      setCurrent(''); setNext('');
      setMessage({ ok: true, text: 'Password changed. Other sessions have been signed out.' });
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    }
  }

  const header = (
    <>
      <h2>Account</h2>
      <p className="muted">
        Signed in as <strong>{user.username}</strong> ({user.role}) with a{' '}
        {session?.method === 'pam' ? 'system (PAM)' : 'HomeAcces'} account
      </p>
    </>
  );

  if (user.auth_source === 'pam') {
    return (
      <>
        <div className="card narrow">
          {header}
          <p>Your password and role come from the server's system account. Change the password with{' '}
            <code>passwd</code> in the terminal.</p>
        </div>
        <AvatarSettings />
      </>
    );
  }

  return (
    <>
      <form className="card narrow" onSubmit={submit}>
        {header}
        <label>
          Current password
          <PasswordInput value={current} onChange={(e) => setCurrent(e.target.value)}
                 autoComplete="current-password" required />
        </label>
        <label>
          New password
          <PasswordInput value={next} onChange={(e) => setNext(e.target.value)}
                 autoComplete="new-password" minLength={8} required />
        </label>
        {message && <p className={message.ok ? 'success' : 'error'}>{message.text}</p>}
        <button type="submit">Change password</button>
      </form>
      <AvatarSettings />
    </>
  );
}
