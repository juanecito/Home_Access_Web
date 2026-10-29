import { Navigate, NavLink, Route, Routes } from 'react-router-dom';
import Users from './Users.jsx';
import Settings from './Settings.jsx';
import Activity from './Activity.jsx';

export default function Admin() {
  return (
    <section>
      <nav className="tabs" aria-label="Administration">
        <NavLink to="users">Users</NavLink>
        <NavLink to="settings">Settings</NavLink>
        <NavLink to="activity">Activity log</NavLink>
      </nav>
      <Routes>
        <Route index element={<Navigate to="users" replace />} />
        <Route path="users" element={<Users />} />
        <Route path="settings" element={<Settings />} />
        <Route path="activity" element={<Activity />} />
        <Route path="*" element={<Navigate to="users" replace />} />
      </Routes>
    </section>
  );
}
