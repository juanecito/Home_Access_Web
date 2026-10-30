import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api.js';
import { destination } from './SshSection.jsx';
import { useSshSessions } from './SshSessions.jsx';

function ProfileForm({ initial, onSave, onCancel, submitLabel }) {
  const [name, setName] = useState(initial?.name ?? '');
  const [host, setHost] = useState(initial?.host ?? '');
  const [port, setPort] = useState(String(initial?.port ?? 22));
  const [username, setUsername] = useState(initial?.username ?? '');
  // The saved key is never sent back: an empty field keeps it, unless "remove" is ticked.
  const [key, setKey] = useState('');
  const [removeKey, setRemoveKey] = useState(false);
  const [error, setError] = useState('');

  async function loadKeyFile(e) {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    if (file.size > 16 * 1024) { setError('This file is too large to be a private key.'); return; }
    setKey(await file.text());
    setRemoveKey(false);
  }

  async function submit(e) {
    e.preventDefault();
    setError('');
    const body = { name, host, port: Number(port), username };
    if (key.trim()) body.private_key = key;
    else if (removeKey) body.private_key = null;
    try {
      await onSave(body);
      if (!initial) { setName(''); setHost(''); setPort('22'); setUsername(''); setKey(''); }
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <form className="item-form" onSubmit={submit}>
      <label>
        <span>Name</span>
        <input placeholder="e.g. Living-room Pi" value={name} onChange={(e) => setName(e.target.value)}
               maxLength={64} required />
      </label>
      <div className="ssh-target">
        <label>
          <span>Host</span>
          <input placeholder="host name or IP address" value={host} onChange={(e) => setHost(e.target.value)}
                 maxLength={255} required autoCapitalize="off" spellCheck={false} />
        </label>
        <label>
          <span>Port</span>
          <input type="number" min={1} max={65535} value={port} onChange={(e) => setPort(e.target.value)} required />
        </label>
        <label>
          <span>Remote user</span>
          <input placeholder="e.g. pi" value={username} onChange={(e) => setUsername(e.target.value)}
                 maxLength={64} required autoCapitalize="off" spellCheck={false} />
        </label>
      </div>
      <label>
        <span>
          Private key <small className="muted">(optional{initial?.has_key ? '; a key is saved, leave empty to keep it' : ''})</small>
        </span>
        <textarea className="mono" rows={3} value={key} spellCheck={false}
                  placeholder={'-----BEGIN OPENSSH PRIVATE KEY-----\n…'}
                  onChange={(e) => { setKey(e.target.value); setRemoveKey(false); }} />
      </label>
      <div className="row wrap">
        <label className="button secondary file-button">
          Load key file…
          <input type="file" onChange={loadKeyFile} />
        </label>
        {initial?.has_key && !key.trim() && (
          <label className="check">
            <input type="checkbox" checked={removeKey} onChange={(e) => setRemoveKey(e.target.checked)} />
            Remove the saved key
          </label>
        )}
      </div>
      <p className="muted">
        Passwords are never saved: you type them in the terminal. If you save a key, prefer one protected by
        a passphrase; it is asked for in the terminal too.
      </p>
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button type="submit">{submitLabel}</button>
        {onCancel && <button type="button" className="secondary" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}

export default function SshConnections() {
  const navigate = useNavigate();
  const { open } = useSshSessions();
  const [profiles, setProfiles] = useState([]);
  const [enabled, setEnabled] = useState(true);
  const [editing, setEditing] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const data = await api.get('/api/ssh/profiles');
      setProfiles(data.profiles);
      setEnabled(data.enabled);
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = async (body) => { await api.post('/api/ssh/profiles', body); await load(); };
  const update = async (id, body) => { await api.put(`/api/ssh/profiles/${id}`, body); setEditing(null); await load(); };

  async function remove(profile) {
    if (!confirm(`Delete the SSH connection "${profile.name}"?`)) return;
    try { await api.del(`/api/ssh/profiles/${profile.id}`); await load(); }
    catch (err) { setMessage({ ok: false, text: err.message }); }
  }

  async function forgetHostKey(profile) {
    if (!confirm(`Forget the saved host key of ${profile.host}? Do this only if you know the server's key `
                 + 'changed (e.g. it was reinstalled). You will be asked to check the new key when you connect.')) return;
    try {
      const { removed } = await api.del(`/api/ssh/profiles/${profile.id}/host-key`);
      setMessage({ ok: true, text: removed ? `Forgot the host key of ${profile.host}.` : `No host key of ${profile.host} was saved.` });
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    }
  }

  return (
    <section>
      <div className="card">
        <h2>SSH connections</h2>
        {!enabled && <p className="error">The SSH terminal is disabled by an administrator.</p>}
        {message && <p className={message.ok ? 'success' : 'error'}>{message.text}</p>}
        {profiles.length === 0 && <p className="muted">No saved connections yet. Add one below.</p>}
        <ul className="items">
          {profiles.map((profile) => (
            <li key={profile.id}>
              {editing === profile.id ? (
                <ProfileForm initial={profile} submitLabel="Save"
                             onSave={(body) => update(profile.id, body)} onCancel={() => setEditing(null)} />
              ) : (
                <>
                  <div className="grow">
                    <strong>{profile.name}</strong>
                    {profile.has_key && <> <span className="badge">key</span></>}
                    <p><code>{destination(profile)}</code></p>
                  </div>
                  <div className="row wrap end">
                    <button disabled={!enabled} onClick={() => navigate(`/ssh/t/${open(profile)}`)}>Connect</button>
                    <button className="secondary" onClick={() => setEditing(profile.id)}>Edit</button>
                    <button className="secondary" onClick={() => forgetHostKey(profile)}>Forget host key</button>
                    <button className="danger" onClick={() => remove(profile)}>Delete</button>
                  </div>
                </>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h2>New connection</h2>
        <ProfileForm onSave={create} submitLabel="Add" />
      </div>
    </section>
  );
}
