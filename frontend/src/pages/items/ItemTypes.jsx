import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api.js';
import { useAuth } from '../../AuthContext.jsx';
import { emptyValue, FIELD_TYPES, FieldInput, formatValue, toJson, toRaw } from './fields.jsx';

let nextKey = 0;

function TypeForm({ initial, onSave, onCancel, submitLabel }) {
  const [name, setName] = useState(initial?.name ?? '');
  const [fields, setFields] = useState(() => (initial?.fields ?? []).map((f) => (
    { key: nextKey++, id: f.id, name: f.name, type: f.type, def: toRaw(f.type, f.default) })));
  const [error, setError] = useState('');

  const change = (key, patch) => setFields(fields.map((f) => (f.key === key ? { ...f, ...patch } : f)));
  const add = () => setFields([...fields, { key: nextKey++, id: null, name: '', type: 'string', def: '' }]);
  const remove = (key) => setFields(fields.filter((f) => f.key !== key));

  async function submit(e) {
    e.preventDefault();
    setError('');
    const kept = new Set(fields.map((f) => f.id));
    const dropped = (initial?.fields ?? []).filter((f) => !kept.has(f.id)).map((f) => f.name);
    if (dropped.length && initial.item_count
        && !confirm(`Removing ${dropped.join(', ')} deletes this data from ${initial.item_count} item(s). Continue?`)) {
      return;
    }
    try {
      await onSave({
        name,
        fields: fields.map((f) => ({ id: f.id ?? undefined, name: f.name, type: f.type, default: toJson(f.type, f.def) })),
      });
      if (!initial) { setName(''); setFields([]); }
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <form className="item-form" onSubmit={submit}>
      <input placeholder="Type name" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} required />
      {fields.length > 0 && (
        <div className="field-rows">
          <span className="muted">Field name</span><span className="muted">Type</span>
          <span className="muted">Default value</span><span />
          {fields.map((f) => (
            <div className="field-row" key={f.key}>
              <input placeholder="e.g. price" value={f.name} aria-label="Field name" required
                     pattern="[a-z][a-z0-9_]{0,39}" title="Lowercase letters, digits and _, starting with a letter"
                     onChange={(e) => change(f.key, { name: e.target.value })} />
              <select value={f.type} aria-label="Field type" disabled={f.id != null}
                      title={f.id != null ? 'The type of an existing field cannot be changed' : undefined}
                      onChange={(e) => change(f.key, { type: e.target.value, def: emptyValue(e.target.value) })}>
                {FIELD_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <FieldInput type={f.type} value={f.def} aria-label="Default value"
                          onChange={(def) => change(f.key, { def })} />
              <button type="button" className="danger" onClick={() => remove(f.key)}>Remove</button>
            </div>
          ))}
        </div>
      )}
      {error && <p className="error">{error}</p>}
      <div className="row">
        <button type="button" className="secondary" onClick={add}>Add field</button>
        <button type="submit">{submitLabel}</button>
        {onCancel && <button type="button" className="secondary" onClick={onCancel}>Cancel</button>}
      </div>
    </form>
  );
}

export default function ItemTypes() {
  const { user } = useAuth();
  const isAdmin = user.role === 'admin';
  const [types, setTypes] = useState([]);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setTypes((await api.get('/api/item-types')).item_types);
      setError('');
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const create = async (body) => { await api.post('/api/item-types', body); await load(); };
  const update = async (id, body) => { await api.put(`/api/item-types/${id}`, body); setEditing(null); await load(); };

  async function remove(type) {
    if (!confirm(`Delete the item type "${type.name}"?`)) return;
    try { await api.del(`/api/item-types/${type.id}`); await load(); }
    catch (err) { setError(err.message); }
  }

  return (
    <>
      {isAdmin && (
        <div className="card">
          <h2>New item type</h2>
          <TypeForm onSave={create} submitLabel="Create type" />
        </div>
      )}

      <div className="card">
        <h2>Item types <span className="muted">({types.length})</span></h2>
        {!isAdmin && <p className="muted">Only administrators can create or change item types.</p>}
        {error && <p className="error">{error}</p>}
        {types.length === 0 && <p className="muted">No item types yet.</p>}
        <ul className="items">
          {types.map((type) => (
            <li key={type.id}>
              {editing === type.id ? (
                <TypeForm initial={type} submitLabel="Save"
                          onSave={(body) => update(type.id, body)} onCancel={() => setEditing(null)} />
              ) : (
                <>
                  <div className="grow">
                    <strong>{type.name}</strong>
                    {type.fields.length === 0 ? <p className="muted">No fields</p> : (
                      <dl className="fields">
                        {type.fields.map((f) => (
                          <div key={f.id}>
                            <dt>{f.name}</dt>
                            <dd><code>{f.type}</code> · default {formatValue(f.type, f.default)}</dd>
                          </div>
                        ))}
                      </dl>
                    )}
                    <small className="muted">
                      {type.item_count} item(s) · table <code>{type.table}</code>
                    </small>
                  </div>
                  {isAdmin && (
                    <>
                      <button className="secondary" onClick={() => setEditing(type.id)}>Edit</button>
                      <button className="danger" onClick={() => remove(type)} disabled={type.item_count > 0}
                              title={type.item_count > 0 ? 'Delete the items of this type first' : undefined}>
                        Delete
                      </button>
                    </>
                  )}
                </>
              )}
            </li>
          ))}
        </ul>
      </div>
    </>
  );
}
