import { defineConfig } from '../../frontend/node_modules/vitest/dist/config.js'
import vue from '../../frontend/node_modules/@vitejs/plugin-vue/dist/index.mjs'
import { fileURLToPath } from 'node:url'

// 审计专用配置，原有前端源码与测试配置保持不变。
const root = fileURLToPath(new URL('../../frontend/', import.meta.url))
export default defineConfig({
  root,
  plugins: [vue()],
  test: { environment: 'jsdom', include: ['../backend/tests/audit_frontend.test.js'] },
})
