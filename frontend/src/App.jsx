import { Navigate, NavLink, Route, Routes } from 'react-router-dom';
import { useAuth } from './AuthContext.jsx';
import Avatar from './Avatar.jsx';
import Login from './pages/Login.jsx';
import ItemsSection from './pages/items/ItemsSection.jsx';
import Account from './pages/Account.jsx';
import Admin from './pages/admin/Admin.jsx';
import Terminal from './pages/Terminal.jsx';

function Protected({ children, admin = false, pam = false }) {
  const { user, session } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (admin && user.role !== 'admin') return <Navigate to="/" replace />;
  if (pam && session?.method !== 'pam') return <Navigate to="/" replace />;
  return children;
}

export default function App() {
  const { user, session, loading, logout } = useAuth();

  if (loading) return <p className="center muted">Loading…</p>;

  return (
    <>
      <header className="topbar">
        <span className="brand">HomeAcces</span>
        {user && (
          <nav>
            <NavLink to="/items">Items</NavLink>
            {session?.method === 'pam' && <NavLink to="/terminal">Terminal</NavLink>}
            {user.role === 'admin' && <NavLink to="/admin">Admin</NavLink>}
            <NavLink to="/account" className="account-link"><Avatar user={user} size={28} />{user.username}</NavLink>
            <button className="link" onClick={logout}>Log out</button>
          </nav>
        )}
      </header>
      <main>
        <Routes>
          <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
          <Route path="/" element={<Navigate to="/items" replace />} />
          <Route path="/items/*" element={<Protected><ItemsSection /></Protected>} />
          <Route path="/account" element={<Protected><Account /></Protected>} />
          <Route path="/terminal" element={<Protected pam><Terminal /></Protected>} />
          <Route path="/admin/*" element={<Protected admin><Admin /></Protected>} />
          <Route path="/users" element={<Navigate to="/admin/users" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
