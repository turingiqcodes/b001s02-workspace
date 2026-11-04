import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The browser only talks to the backend (never to Google). /api is proxied to FastAPI.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true } },
  },
});
