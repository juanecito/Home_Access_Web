import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { matchPath, useLocation } from 'react-router-dom';
import { api } from '../../api.js';
import TerminalView from '../../TerminalView.jsx';
import { destination } from './SshSection.jsx';

// Open SSH sessions, one tab each under /ssh/t/<sid>. They live above the routes so that they stay
// connected while the user visits other pages; a page reload or logging out closes them all.
// homeacces-ssh limits how many run at once per user (HOMEACCES_SSH_MAX_PER_USER) and refuses extra
// ones with a close reason shown in their terminal.
const SshSessionsContext = createContext(null);

export function useSshSessions() {
  return useContext(SshSessionsContext);
}

// "NAS", then "NAS (2)", "NAS (3)"… for more sessions to the same connection.
function tabLabel(sessions, profile) {
  const taken = new Set(sessions.filter((s) => s.profile.id === profile.id).map((s) => s.label));
  let label = profile.name;
  for (let n = 2; taken.has(label); n++) label = `${profile.name} (${n})`;
  return label;
}

export function SshSessionsProvider({ children }) {
  const [sessions, setSessions] = useState([]);
  const nextId = useRef(1);

  const open = useCallback((profile) => {
    const id = nextId.current++;
    setSessions((list) => [...list, { id, profile, label: tabLabel(list, profile), status: 'connecting' }]);
    return id;
  }, []);
  const close = useCallback((id) => setSessions((list) => list.filter((s) => s.id !== id)), []);
  const setStatus = useCallback((id, status) =>
    setSessions((list) => list.map((s) => (s.id === id && s.status !== status ? { ...s, status } : s))), []);

  // Leaving or reloading the page drops every SSH session: ask first while one is still connected.
  const anyConnected = sessions.some((s) => s.status === 'connected');
  useEffect(() => {
    if (!anyConnected) return undefined;
    const warn = (e) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [anyConnected]);

  const value = useMemo(() => ({ sessions, open, close, setStatus }), [sessions, open, close, setStatus]);
  return <SshSessionsContext.Provider value={value}>{children}</SshSessionsContext.Provider>;
}

// The terminals of all open sessions; only the one of the current /ssh/t/<sid> tab is shown.
export function SshTerminals() {
  const { sessions, setStatus } = useSshSessions();
  const match = matchPath('/ssh/t/:sid', useLocation().pathname);
  const activeId = match ? Number(match.params.sid) : null;

  return (
    <div className="ssh-terminals">
      {sessions.map(({ id, profile }) => (
        <TerminalView
          key={id}
          active={id === activeId}
          title={<>{profile.name} <span className="muted">— {destination(profile)}</span></>}
          wsPath="/api/ssh/ws"
          getTicket={async () => (await api.post('/api/ssh/ticket', { profile_id: profile.id })).ticket}
          onStatus={(status) => setStatus(id, status)}
        />
      ))}
    </div>
  );
}
