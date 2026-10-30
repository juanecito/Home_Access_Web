import { api } from '../api.js';
import { useAuth } from '../AuthContext.jsx';
import TerminalView from '../TerminalView.jsx';

// Login shell on this machine, served by homeacces-privd (PAM sessions only).
export default function Terminal() {
  const { user } = useAuth();
  return (
    <TerminalView
      title={<>Terminal <span className="muted">— {user.username}@{location.hostname}</span></>}
      wsPath="/api/terminal/ws"
      getTicket={async () => (await api.post('/api/terminal/ticket')).ticket}
    />
  );
}
