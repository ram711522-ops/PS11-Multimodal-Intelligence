import { defineConfig, loadEnv } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  const apiTarget = env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

  return {
    plugins: [tailwindcss()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      proxy: {
        '/health': { target: apiTarget, changeOrigin: true },
        '/api': { target: apiTarget, changeOrigin: true },
      },
    },
  };
});
