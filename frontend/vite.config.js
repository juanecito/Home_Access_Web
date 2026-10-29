import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // During development, forward API calls to the Flask dev server.
    proxy: {
      '/api/terminal/ws': { target: 'ws://127.0.0.1:8001', ws: true },
      '/api': 'http://127.0.0.1:8000',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // The minifier drops license comments, so ship the licenses of all bundled dependencies
    // (React, xterm.js, …) as /licenses.md instead, as their licenses require.
    license: { fileName: 'licenses.md' },
  },
});
