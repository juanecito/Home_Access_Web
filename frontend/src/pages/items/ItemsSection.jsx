import { Navigate, NavLink, Route, Routes } from 'react-router-dom';
import Items from './Items.jsx';
import ItemTypes from './ItemTypes.jsx';

export default function ItemsSection() {
  return (
    <section>
      <nav className="tabs" aria-label="Items">
        <NavLink to="/items" end>Items</NavLink>
        <NavLink to="/items/types">Item types</NavLink>
      </nav>
      <Routes>
        <Route index element={<Items />} />
        <Route path="types" element={<ItemTypes />} />
        <Route path="*" element={<Navigate to="/items" replace />} />
      </Routes>
    </section>
  );
}
