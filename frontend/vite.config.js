import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// WeAidStock 화면 포트: 기본 3300 (3000 은 다른 앱과 겹쳐서 변경)
// 바꾸려면 환경변수 WEAID_WEB_PORT 사용. 사용 중이면 3301, 3302 … 로 자동 이동합니다.
const port = Number(process.env.WEAID_WEB_PORT || 3300)

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router'],
          'vendor-d3': ['d3'],
          'vendor-http': ['axios']
        }
      }
    }
  },
  server: {
    host: '127.0.0.1',
    port,
    strictPort: false,
    open: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5001',
        changeOrigin: true,
        secure: false
      }
    }
  }
})
