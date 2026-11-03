import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: process.env.AGENTEVAL_DEV_API || 'http://127.0.0.1:8000' },
      '/health': { target: process.env.AGENTEVAL_DEV_API || 'http://127.0.0.1:8000' },
    },
  },
});
