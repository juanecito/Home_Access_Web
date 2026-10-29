import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../api.js';
import { useAuth } from '../../AuthContext.jsx';
import Avatar from '../../Avatar.jsx';
import PasswordInput from '../../PasswordInput.jsx';

// Local accounts only: system (PAM) accounts get their name, password and role from the Pi.
function EditUser({ user, isSelf, onClose, onSaved }) {
  const dialogRef = useRef(null);
  const [form, setForm] = useState({ username: user.username, role: user.role, password: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { dialogRef.current.showModal(); }, []);

  async function save(e) {
    e.preventDefault();
    const changes = {};
    if (form.username !== user.username) changes.username = form.username;
    if (form.role !== user.role) changes.role = form.role;
    if (form.password) changes.password = form.password;
    if (!Object.keys(changes).length) return onClose();
    setBusy(true);
    try { await api.put(`/api/users/${user.id}`, changes); onSaved(); }
    catch (err) { setError(err.message); setBusy(false); }
  }

  return (
    <dialog ref={dialogRef} onClose={onClose}>
      <form className="dialog-form" onSubmit={save}>
        <h2>Edit {user.username}</h2>
        <label>
          Username
          <input value={form.username} required autoCapitalize="none"
                 onChange={(e) => setForm({ ...form, username: e.target.value })} />
        </label>
        <label>
          Role
          <select value={form.role} disabled={isSelf}
                  title={isSelf ? 'You cannot change your own role' : undefined}
                  onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
        </label>
        <label>
          New password <span className="muted">(leave empty to keep; signs the user out)</span>
          <PasswordInput value={form.password} minLength={8} autoComplete="new-password"
                 onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="row end">
          <button type="button" className="secondary" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy}>Save</button>
        </div>
      </form>
    </dialog>
  );
}

export default function Users() {
  const { user: me, reloadUser } = useAuth();
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState({ username: '', password: '', role: 'user' });
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try { setUsers((await api.get('/api/users')).users); setError(''); }
    catch (err) { setError(err.message); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function run(action) {
    setError('');
    try { await action(); await load(); }
    catch (err) { setError(err.message); }
  }

  const create = (e) => {
    e.preventDefault();
    run(async () => {
      await api.post('/api/users', form);
      setForm({ username: '', password: '', role: 'user' });
    });
  };

  const remove = (u) => {
    if (confirm(`Delete user ${u.username} and all their items?`)) run(() => api.del(`/api/users/${u.id}`));
  };

  return (
    <>
      <form className="card" onSubmit={create}>
        <h2>New user</h2>
        <div className="row wrap">
          <input placeholder="Username" value={form.username} required
                 onChange={(e) => setForm({ ...form, username: e.target.value })} />
          <PasswordInput placeholder="Password" value={form.password} minLength={8} required
                 autoComplete="new-password" onChange={(e) => setForm({ ...form, password: e.target.value })} />
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
          <button type="submit">Create</button>
        </div>
      </form>

      <div className="card">
        <h2>Users</h2>
        {error && <p className="error">{error}</p>}
        <table>
          <thead>
            <tr><th>ID</th><th>Username</th><th>Source</th><th>Role</th><th>Created</th><th>Last sign-in</th><th /></tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.id}</td>
                <td><span className="row"><Avatar user={u} size={28} /><span>{u.username}{u.id === me.id && <span className="muted"> (you)</span>}</span></span></td>
                <td>{u.auth_source === 'pam' ? 'system' : 'local'}</td>
                <td>{u.role}</td>
                <td>{new Date(u.created_at).toLocaleDateString()}</td>
                <td>{u.last_login ? new Date(u.last_login).toLocaleString() : <span className="muted">never</span>}</td>
                <td className="actions">
                  <button className="secondary" onClick={() => setEditing(u)}
                          disabled={u.auth_source === 'pam'}
                          title={u.auth_source === 'pam' ? 'Managed by the system' : undefined}>Edit</button>
                  <button className="danger" onClick={() => remove(u)} disabled={u.id === me.id}>Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <EditUser user={editing} isSelf={editing.id === me.id} onClose={() => setEditing(null)}
                  onSaved={() => { if (editing.id === me.id) reloadUser(); setEditing(null); load(); }} />
      )}
    </>
  );
}
