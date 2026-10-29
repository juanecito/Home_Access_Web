import { useRef, useState } from 'react';
import { api } from './api.js';
import { useAuth } from './AuthContext.jsx';
import Avatar from './Avatar.jsx';

const SIZE = 60;
const TYPES = ['image/png', 'image/jpeg'];

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(new Error('Could not read the file'));
    reader.readAsDataURL(file);
  });
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('The file is not a valid image'));
    img.src = src;
  });
}

// Returns a 60x60 data URL: images already at that size are kept as they are,
// others are cropped to a centred square and scaled down in the same format.
async function toAvatar(file) {
  const src = await readAsDataUrl(file);
  const img = await loadImage(src);
  if (img.naturalWidth === SIZE && img.naturalHeight === SIZE) return src;
  const side = Math.min(img.naturalWidth, img.naturalHeight);
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = SIZE;
  const ctx = canvas.getContext('2d');
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, SIZE, SIZE);
  return canvas.toDataURL(file.type, 0.9);
}

export default function AvatarSettings() {
  const { user, reloadUser } = useAuth();
  const fileRef = useRef(null);
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  async function choose(e) {
    const file = e.target.files[0];
    e.target.value = '';
    if (!file) return;
    setMessage(null);
    if (!TYPES.includes(file.type)) {
      setMessage({ ok: false, text: 'Choose a PNG or JPEG image.' });
      return;
    }
    try {
      setPending(await toAvatar(file));
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    }
  }

  async function run(action, text) {
    setBusy(true);
    try {
      await action();
      await reloadUser();
      setPending(null);
      setMessage({ ok: true, text });
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card narrow">
      <h2>Avatar</h2>
      <div className="row">
        <Avatar user={pending ? { ...user, avatar: pending } : user} />
        <div className="avatar-actions">
          <input ref={fileRef} type="file" accept={TYPES.join(',')} hidden onChange={choose} />
          {pending ? (
            <div className="row wrap">
              <button type="button" disabled={busy}
                      onClick={() => run(() => api.put('/api/auth/avatar', { avatar: pending }), 'Avatar updated.')}>
                Save
              </button>
              <button type="button" className="secondary" disabled={busy} onClick={() => setPending(null)}>
                Cancel
              </button>
            </div>
          ) : (
            <div className="row wrap">
              <button type="button" className="secondary" disabled={busy} onClick={() => fileRef.current.click()}>
                {user.avatar ? 'Change image…' : 'Upload image…'}
              </button>
              {user.avatar && (
                <button type="button" className="danger" disabled={busy}
                        onClick={() => run(() => api.del('/api/auth/avatar'), 'Avatar removed.')}>
                  Remove
                </button>
              )}
            </div>
          )}
          <p className="muted">PNG or JPEG. Larger images are cropped to a square and resized to 60×60 pixels.</p>
        </div>
      </div>
      {message && <p className={message.ok ? 'success' : 'error'}>{message.text}</p>}
    </div>
  );
}
