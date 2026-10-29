import { useState } from 'react';

// Password field with an eye button that toggles the value between hidden and visible.
export default function PasswordInput(props) {
  const [visible, setVisible] = useState(false);
  return (
    <span className="password-input">
      <input {...props} type={visible ? 'text' : 'password'} />
      <button type="button" className="reveal" onClick={() => setVisible(!visible)}
              aria-label={visible ? 'Hide password' : 'Show password'} aria-pressed={visible}
              title={visible ? 'Hide password' : 'Show password'}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
          <circle cx="12" cy="12" r="3" />
          {visible && <line x1="3" y1="3" x2="21" y2="21" />}
        </svg>
      </button>
    </span>
  );
}
