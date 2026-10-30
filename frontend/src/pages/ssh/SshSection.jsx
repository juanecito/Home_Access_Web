import { Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import SshConnections from './SshConnections.jsx';
import { useSshSessions } from './SshSessions.jsx';

// The SSH menu: a "Connections" tab, then one tab per open session. The session terminals
// themselves are rendered by SshTerminals (App.jsx), below this tab bar.
export default function SshSection() {
  const { sessions, close } = useSshSessions();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  function closeTab(session) {
    if (session.status === 'connected' && !confirm(`Close the SSH session "${session.label}"?`)) return;
    if (pathname === `/ssh/t/${session.id}`) {
      // Show the tab to the right, else the one to the left, else the connections.
      const index = sessions.findIndex((s) => s.id === session.id);
      const next = sessions[index + 1] ?? sessions[index - 1];
      navigate(next ? `/ssh/t/${next.id}` : '/ssh', { replace: true });
    }
    close(session.id);
  }

  return (
    <>
      <nav className="tabs ssh-tabs" aria-label="SSH sessions">
        <NavLink to="/ssh" end>Connections</NavLink>
        {sessions.map((session) => (
          <span key={session.id} className="ssh-tab">
            <NavLink to={`/ssh/t/${session.id}`} title={`${session.label}: ${session.status}`}
                     onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); closeTab(session); } }}>
              <span className={`dot ${session.status}`} aria-hidden="true" />
              {session.label}
            </NavLink>
            <button className="tab-close" title="Close" aria-label={`Close ${session.label}`}
                    onClick={() => closeTab(session)}>×</button>
          </span>
        ))}
      </nav>
      <Routes>
        <Route index element={<SshConnections />} />
        <Route path="t/:sid" element={<SessionTab />} />
        <Route path="*" element={<Navigate to="/ssh" replace />} />
      </Routes>
    </>
  );
}

// The terminal is drawn by SshTerminals; this route only sends unknown sessions (e.g. after a
// reload) back to the connections.
function SessionTab() {
  const sid = Number(useParams().sid);
  const { sessions } = useSshSessions();
  return sessions.some((s) => s.id === sid) ? null : <Navigate to="/ssh" replace />;
}

// user@host[:port], with brackets around IPv6 addresses when a port is shown.
export function destination(profile) {
  const host = profile.host.includes(':') && profile.port !== 22 ? `[${profile.host}]` : profile.host;
  return `${profile.username}@${host}${profile.port !== 22 ? `:${profile.port}` : ''}`;
}
