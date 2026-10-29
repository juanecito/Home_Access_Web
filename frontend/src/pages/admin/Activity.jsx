import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api.js';

const PAGE = 50;

const EVENT_LABELS = {
  'auth.login': 'Sign in',
  'auth.logout': 'Sign out',
  'auth.register': 'Registration',
  'auth.password_change': 'Password change',
  'auth.token_reuse': 'Stolen session detected',
  'auth.session_revoked': 'Session revoked',
  'terminal.open': 'Terminal opened',
  'user.create': 'User created',
  'user.update': 'User changed',
  'user.delete': 'User deleted',
  'settings.update': 'Settings changed',
  'item.create': 'Item created',
  'item.update': 'Item changed',
  'item.delete': 'Item deleted',
};

const GROUPS = [['auth.', 'All sign-in events'], ['user.', 'All user admin'], ['item.', 'All item changes']];

const formatDetail = (v) => (Array.isArray(v) ? v.join(', ') : String(v));

export default function Activity() {
  const [filters, setFilters] = useState({ user: '', event: '', result: '' });
  const [applied, setApplied] = useState(filters);
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: PAGE, offset });
    Object.entries(applied).forEach(([k, v]) => v && params.set(k, v));
    try { setData(await api.get(`/api/admin/activity?${params}`)); setError(''); }
    catch (err) { setError(err.message); }
  }, [applied, offset]);

  useEffect(() => { load(); }, [load]);

  const apply = (next) => { setApplied(next); setOffset(0); };
  const submit = (e) => { e.preventDefault(); apply(filters); };
  const change = (key, value, now = false) => {
    const next = { ...filters, [key]: value };
    setFilters(next);
    if (now) apply(next);  // selects apply immediately; the user field on submit
  };

  const total = data?.total ?? 0;
  return (
    <div className="card">
      <div className="row spread wrap">
        <h2>Activity log</h2>
        <button className="secondary" onClick={load}>Refresh</button>
      </div>
      <form className="row wrap filters" onSubmit={submit}>
        <input placeholder="Username" value={filters.user} onChange={(e) => change('user', e.target.value)} />
        <select value={filters.event} onChange={(e) => change('event', e.target.value, true)}>
          <option value="">All events</option>
          {GROUPS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          {data?.events.map((ev) => <option key={ev} value={ev}>{EVENT_LABELS[ev] ?? ev}</option>)}
        </select>
        <select value={filters.result} onChange={(e) => change('result', e.target.value, true)}>
          <option value="">Any result</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
        </select>
        <button type="submit">Filter</button>
      </form>
      {error && <p className="error">{error}</p>}

      <div className="table-wrap">
        <table className="activity">
          <thead>
            <tr><th>Time</th><th>User</th><th>Event</th><th>Result</th><th>IP</th><th>Details</th></tr>
          </thead>
          <tbody>
            {data?.entries.map((e) => (
              <tr key={e.id}>
                <td className="nowrap">{new Date(e.ts).toLocaleString()}</td>
                <td>{e.username ?? <span className="muted">—</span>}</td>
                <td title={e.event}>{EVENT_LABELS[e.event] ?? e.event}</td>
                <td><span className={`badge ${e.success ? 'ok' : 'fail'}`}>{e.success ? 'success' : 'failure'}</span></td>
                <td className="nowrap" title={e.user_agent ?? undefined}>{e.ip}</td>
                <td className="details">
                  {Object.entries(e.details).map(([k, v]) => (
                    <span key={k}><span className="muted">{k}:</span> {formatDetail(v)}</span>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && total === 0 && <p className="muted">No matching entries.</p>}

      {total > 0 && (
        <div className="row spread pager">
          <span className="muted">{offset + 1}–{Math.min(offset + PAGE, total)} of {total}</span>
          <div className="row">
            <button className="secondary" disabled={offset === 0}
                    onClick={() => setOffset(Math.max(offset - PAGE, 0))}>Newer</button>
            <button className="secondary" disabled={offset + PAGE >= total}
                    onClick={() => setOffset(offset + PAGE)}>Older</button>
          </div>
        </div>
      )}
    </div>
  );
}
