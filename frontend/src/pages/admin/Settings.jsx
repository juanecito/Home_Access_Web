import { useEffect, useState } from 'react';
import { api } from '../../api.js';

export default function Settings() {
  const [settings, setSettings] = useState(null);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    api.get('/api/admin/settings').then(setSettings).catch((err) => setMessage({ ok: false, text: err.message }));
  }, []);

  // `card` says where to show the result.
  async function update(card, changes, text) {
    setMessage(null);
    try {
      setSettings(await api.put('/api/admin/settings', changes));
      setMessage({ card, ok: true, text });
    } catch (err) {
      setMessage({ card, ok: false, text: err.message });
    }
  }

  const setRegistration = (allow) =>
    update('registration', { allow_registration: allow }, allow ? 'Registration enabled.' : 'Registration disabled.');
  const setSsh = (enabled) =>
    update('ssh', { ssh_enabled: enabled }, enabled ? 'SSH terminal enabled.' : 'SSH terminal disabled.');

  if (!settings) return message ? <p className="error">{message.text}</p> : <p className="muted">Loading…</p>;

  return (
    <>
      <div className="card">
        <h2>User creation</h2>
        <label className="check">
          <input type="checkbox" checked={settings.allow_registration}
                 onChange={(e) => setRegistration(e.target.checked)} />
          Allow visitors to create their own account
        </label>
        <p className="muted">
          When off, the sign-in page hides “Register” and only admins can create accounts (Users tab).
          System (PAM) accounts can always sign in.
        </p>
        {message?.card === 'registration' && <p className={message.ok ? 'success' : 'error'}>{message.text}</p>}
      </div>

      <div className="card">
        <h2>SSH terminal</h2>
        <label className="check">
          <input type="checkbox" checked={settings.ssh_enabled} onChange={(e) => setSsh(e.target.checked)} />
          Let users open SSH connections from the browser (SSH menu)
        </label>
        <p className="muted">
          Every user can save SSH connections and open them in a terminal. The SSH client runs on this
          machine as the unprivileged service user, so connections come from this machine's address.
          When off, saved connections are kept but cannot be opened; open sessions are not closed.
        </p>
        {message?.card === 'ssh' && <p className={message.ok ? 'success' : 'error'}>{message.text}</p>}
      </div>

      <div className="card">
        <h2>Activity log</h2>
        <p>
          Sign-ins, sign-outs, account changes and item changes are recorded in the database
          {settings.activity_retention_days > 0 && <> for {settings.activity_retention_days} days</>}
          {settings.activity_log_file
            ? <> and appended to <code>{settings.activity_log_file}</code> on the server.</>
            : '. Writing to a log file is disabled.'}
        </p>
      </div>
    </>
  );
}
