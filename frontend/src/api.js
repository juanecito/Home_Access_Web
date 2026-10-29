// Thin fetch wrapper: sends cookies, adds the CSRF header on writes and
// transparently refreshes the access token once when it has expired.

export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function readCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : '';
}

async function send(method, path, body) {
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (!['GET', 'HEAD'].includes(method)) headers['X-CSRF-Token'] = readCookie('csrf_token');
  return fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
}

let refreshing = null;

function refreshSession() {
  // Share one in-flight refresh between concurrent requests.
  refreshing ??= send('POST', '/api/auth/refresh').finally(() => { refreshing = null; });
  return refreshing;
}

export async function request(method, path, body) {
  let res = await send(method, path, body);
  // The access cookie may be expired or already dropped by the browser: try a refresh once.
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    if ((await refreshSession()).ok) {
      res = await send(method, path, body);
    } else {
      window.dispatchEvent(new Event('session-expired'));
    }
  }
  if (res.status === 204) return null;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error || res.statusText);
  return data;
}

export const api = {
  get: (path) => request('GET', path),
  post: (path, body) => request('POST', path, body ?? {}),
  put: (path, body) => request('PUT', path, body),
  del: (path) => request('DELETE', path),
};
