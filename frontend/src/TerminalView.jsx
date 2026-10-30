import { useEffect, useRef, useState } from 'react';
import { Terminal as XTerm } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

// Narrower windows keep this width and scroll horizontally instead of squeezing full-screen programs.
const MIN_COLS = 120;

// An xterm.js terminal connected to a terminal WebSocket (homeacces-privd or homeacces-ssh, see
// backend/webterm.py). `getTicket` fetches a single-use ticket from the API for each connection.
// Protocol:
//   client → server: JSON text {type: "auth", ticket, cols, rows} first, then
//                    {type: "input", data} and {type: "resize", cols, rows}
//   server → client: binary frames with raw terminal output; close reason on exit.
// An inactive view stays mounted (and connected) but hidden, as for background SSH tabs.
export default function TerminalView({ title, wsPath, getTicket, actions, active = true, onStatus }) {
  const containerRef = useRef(null);
  const hostRef = useRef(null);
  const termRef = useRef(null);
  const getTicketRef = useRef(getTicket);
  getTicketRef.current = getTicket;
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;
  const [status, setStatus] = useState('connecting');
  const [session, setSession] = useState(0);

  useEffect(() => { onStatusRef.current?.(status); }, [status]);

  useEffect(() => {
    const term = new XTerm({
      cursorBlink: true,
      fontFamily: 'ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace',
      fontSize: 14,
      scrollback: 5000,
      theme: { background: '#101216', foreground: '#e6e8ec', cursor: '#e0446f' },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(hostRef.current);
    const fitTerm = () => {
      const dims = fit.proposeDimensions();
      if (!dims || Number.isNaN(dims.cols) || Number.isNaN(dims.rows)) return;
      const cols = Math.max(dims.cols, MIN_COLS);
      if (cols !== term.cols || dims.rows !== term.rows) term.resize(cols, dims.rows);
    };
    fitTerm();
    termRef.current = { term, fitTerm };

    let ws = null;
    let disposed = false;
    const send = (msg) => ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify(msg));

    (async () => {
      try {
        const ticket = await getTicketRef.current();
        if (disposed) return;
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        ws = new WebSocket(`${proto}://${location.host}${wsPath}`);
        ws.binaryType = 'arraybuffer';
        ws.onopen = () => {
          send({ type: 'auth', ticket, cols: term.cols, rows: term.rows });
          setStatus('connected');
          term.focus();
        };
        ws.onmessage = (e) => term.write(typeof e.data === 'string' ? e.data : new Uint8Array(e.data));
        ws.onclose = (e) => {
          if (disposed) return;
          setStatus('closed');
          term.write(`\r\n\x1b[2m[${e.reason || 'connection closed'}]\x1b[0m\r\n`);
        };
      } catch (err) {
        if (disposed) return;
        setStatus('closed');
        term.write(`\x1b[31m${err.message}\x1b[0m\r\n`);
      }
    })();

    const onData = term.onData((data) => send({ type: 'input', data }));
    const onResize = term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }));
    // The host shrinks when the horizontal scrollbar appears, so watch it too to drop a row.
    const observer = new ResizeObserver(fitTerm);
    observer.observe(containerRef.current);
    observer.observe(hostRef.current);

    return () => {
      disposed = true;
      observer.disconnect();
      onData.dispose();
      onResize.dispose();
      ws?.close();
      term.dispose();
      termRef.current = null;
    };
  }, [session, wsPath]);

  // Hidden terminals keep receiving output: fit and repaint when shown again.
  useEffect(() => {
    if (!active || !termRef.current) return;
    const { term, fitTerm } = termRef.current;
    fitTerm();
    term.refresh(0, term.rows - 1);
    term.focus();
  }, [active]);

  return (
    <section className="terminal-page" hidden={!active}>
      <div className="row spread">
        <h2>{title}</h2>
        <div className="row">
          {actions}
          <span className={`status ${status}`}>{status}</span>
          {status === 'closed' && (
            <button onClick={() => { setStatus('connecting'); setSession((n) => n + 1); }}>Reconnect</button>
          )}
        </div>
      </div>
      <div className="terminal" ref={containerRef}>
        <div className="terminal-host" ref={hostRef} />
      </div>
    </section>
  );
}
