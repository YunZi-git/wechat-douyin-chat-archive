import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把 API / 媒体请求转给本地后端；打包产物由 FastAPI 直接托管。
const BACKEND = process.env.CHATWORK_BACKEND || 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [vue()],
  base: '/',
  server: {
    port: 5173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/media': { target: BACKEND, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
})
