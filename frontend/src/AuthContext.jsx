import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { api } from './api.js';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  // How this session signed in: { method: 'local' | 'pam' }. The terminal needs 'pam'.
  const [session, setSession] = useState(null);
  const [methods, setMethods] = useState({ local: true, pam: false, registration: true });
  const [loading, setLoading] = useState(true);

  const apply = (data) => { setUser(data?.user ?? null); setSession(data?.session ?? null); };

  // Restore the session on page load: try the access token, then the refresh token.
  useEffect(() => {
    api.get('/api/auth/methods').then(setMethods).catch(() => {});
    api.get('/api/auth/me')
      .catch(() => api.post('/api/auth/refresh'))
      .then(apply)
      .catch(() => apply(null))
      .finally(() => setLoading(false));

    const onExpired = () => apply(null);
    window.addEventListener('session-expired', onExpired);
    return () => window.removeEventListener('session-expired', onExpired);
  }, []);

  const login = useCallback(async (username, password, method = 'local') => {
    apply(await api.post('/api/auth/login', { username, password, method }));
  }, []);

  const register = useCallback(async (username, password) => {
    apply(await api.post('/api/auth/register', { username, password }));
  }, []);

  // After an admin edits their own account (e.g. a rename).
  const reloadUser = useCallback(async () => { apply(await api.get('/api/auth/me')); }, []);

  const logout = useCallback(async () => {
    await api.post('/api/auth/logout').catch(() => {});
    apply(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, session, methods, loading, login, register, logout, reloadUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
