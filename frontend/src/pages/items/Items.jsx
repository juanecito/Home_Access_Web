import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api.js';
import { useAuth } from '../../AuthContext.jsx';
import { FieldInput, formatValue, toJson, toRaw } from './fields.jsx';

const PAGE_SIZE = 20;

function fieldValues(type, values) {
  return Object.fromEntries((type?.fields ?? []).map((f) => [f.name, toRaw(f.type, values?.[f.name] ?? f.default)]));
}

function ItemForm({ initial, types, onSave, onCancel, submitLabel }) {
  const [title, setTitle] = useState(initial?.title ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [typeId, setTypeId] = useState(initial?.type_id ?? null);
  const type = types.find((t) => t.id === typeId);
  const [values, setValues] = useState(() => fieldValues(type, initial?.fields));
  const [error, setError] = useState('');

  function chooseType(value) {
    const id = value === '' ? null : Number(value);
    setTypeId(id);
    setValues(fieldValues(types.find((t) => t.id === id)));
  }

  async function submit(e) {
    e.preventDefault();
    setError('');
    const body = { title, description, done: initial?.done ?? false };
    if (type) body.fields = Object.fromEntries(type.fields.map((f) => [f.name, toJson(f.type, values[f.name])]));
    if (!initial) body.type_id = typeId;
    try {
      await onSave(body);
      if (!initial) { setTitle(''); setDescription(''); setValues(fieldValues(type)); }
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <form className="item-form" onSubmit={submit}>
      {initial ? (
        initial.type_name && <span className="muted">Type: {initial.type_name}</span>
      ) : (
        <select value={typeId ?? ''} onChange={(e) => chooseType(e.target.value)} aria-label="Item type">
          <option value="">No type</option>
          {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      )}
      <input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} required />
      <textarea placeholder="Description (optional)" value={description}
                onChange={(e) => setDescription(e.target.value)} maxLength={5000} rows={2} />
      {type?.fields.map((f) => (
        <label key={f.id} className={f.type === 'boolean' ? 'check' : undefined}>
          {f.type !== 'boolean' && <span>{f.name} <small className="muted">({f.type})</small></span>}
          <FieldInput type={f.type} value={values[f.name]} required={f.type !== 'boolean'}
                      onChange={(v) => setValues({ ...values, [f.name]: v })} />
          {f.type === 'boolean' && f.name}
        </label>
      ))}
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button type="submit">{submitLabel}</button>
        {onCancel && <button type="button" className="secondary" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}

export default function Items() {
  const { user } = useAuth();
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [types, setTypes] = useState([]);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
      if (query) params.set('q', query);
      if (typeFilter) params.set('type_id', typeFilter);
      const data = await api.get(`/api/items?${params}`);
      setItems(data.items);
      setTotal(data.total);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }, [offset, query, typeFilter]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    api.get('/api/item-types').then((data) => setTypes(data.item_types)).catch((err) => setError(err.message));
  }, []);

  const create = async (body) => { await api.post('/api/items', body); setOffset(0); await load(); };
  const update = async (id, body) => { await api.put(`/api/items/${id}`, body); setEditing(null); await load(); };

  async function toggle(item) {
    try { await update(item.id, { title: item.title, description: item.description, done: !item.done }); }
    catch (err) { setError(err.message); }
  }

  async function remove(item) {
    if (!confirm(`Delete "${item.title}"?`)) return;
    try { await api.del(`/api/items/${item.id}`); await load(); }
    catch (err) { setError(err.message); }
  }

  return (
    <section>
      <div className="card">
        <h2>New item</h2>
        <ItemForm types={types} onSave={create} submitLabel="Add" />
      </div>

      <div className="card">
        <div className="row spread">
          <h2>Items <span className="muted">({total})</span></h2>
          <div className="row">
            <select value={typeFilter} aria-label="Filter by type"
                    onChange={(e) => { setTypeFilter(e.target.value); setOffset(0); }}>
              <option value="">All types</option>
              <option value="none">No type</option>
              {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <input className="search" type="search" placeholder="Search…" value={query}
                   onChange={(e) => { setQuery(e.target.value); setOffset(0); }} />
          </div>
        </div>
        {error && <p className="error">{error}</p>}
        {items.length === 0 && <p className="muted">Nothing here yet.</p>}
        <ul className="items">
          {items.map((item) => (
            <li key={item.id} className={item.done ? 'done' : ''}>
              {editing === item.id ? (
                <ItemForm initial={item} types={types} submitLabel="Save"
                          onSave={(body) => update(item.id, body)} onCancel={() => setEditing(null)} />
              ) : (
                <>
                  <input type="checkbox" checked={item.done} onChange={() => toggle(item)} aria-label="Done" />
                  <div className="grow">
                    <strong>{item.title}</strong>
                    {item.type_name && <> <span className="badge">{item.type_name}</span></>}
                    {item.description && <p>{item.description}</p>}
                    {item.fields && Object.keys(item.fields).length > 0 && (
                      <dl className="fields">
                        {Object.entries(item.fields).map(([name, value]) => (
                          <div key={name}>
                            <dt>{name}</dt>
                            <dd>{formatValue(typeof value === 'boolean' ? 'boolean' : 'string', value)}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                    <small className="muted">
                      {user.role === 'admin' && `${item.owner} · `}updated {new Date(item.updated_at).toLocaleString()}
                    </small>
                  </div>
                  <button className="secondary" onClick={() => setEditing(item.id)}>Edit</button>
                  <button className="danger" onClick={() => remove(item)}>Delete</button>
                </>
              )}
            </li>
          ))}
        </ul>
        {total > PAGE_SIZE && (
          <div className="row">
            <button className="secondary" disabled={offset === 0}
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
            <span className="muted">{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
            <button className="secondary" disabled={offset + PAGE_SIZE >= total}
                    onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
          </div>
        )}
      </div>
    </section>
  );
}
