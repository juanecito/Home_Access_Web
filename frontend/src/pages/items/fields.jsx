// Inputs and formatting for the typed fields of item types (int, float, boolean, string).
// Number inputs keep their raw text while editing; toJson converts it before sending.

export const FIELD_TYPES = ['int', 'float', 'boolean', 'string'];

export function emptyValue(type) {
  if (type === 'boolean') return false;
  return type === 'string' ? '' : '0';
}

// API value -> form value
export function toRaw(type, value) {
  if (type === 'int' || type === 'float') return value == null ? '' : String(value);
  return value ?? emptyValue(type);
}

// Form value -> API value (invalid numbers become null and are rejected by the server)
export function toJson(type, raw) {
  if (type === 'int' || type === 'float') {
    const n = raw === '' ? NaN : Number(raw);
    return Number.isFinite(n) ? n : null;
  }
  return raw;
}

export function formatValue(type, value) {
  if (type === 'boolean') return value ? 'yes' : 'no';
  return value === '' || value == null ? '—' : String(value);
}

export function FieldInput({ type, value, onChange, ...props }) {
  if (type === 'boolean') {
    return <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} {...props} />;
  }
  if (type === 'int' || type === 'float') {
    return <input type="number" step={type === 'int' ? 1 : 'any'} value={value}
                  onChange={(e) => onChange(e.target.value)} {...props} />;
  }
  return <input value={value} maxLength={5000} onChange={(e) => onChange(e.target.value)} {...props} />;
}
