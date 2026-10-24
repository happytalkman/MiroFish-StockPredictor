import api from './index'

// 로컬 전용 관리 API (이 PC 에서만 동작)
export const getSettings = () => api.get('/api/admin/settings', { timeout: 15000 })
export const saveSettings = (values, clear = []) => api.post('/api/admin/settings', { values, clear }, { timeout: 15000 })
export const testService = (service, value = null) => api.post('/api/admin/test', { service, value }, { timeout: 45000 })
export const getAdminStatus = () => api.get('/api/admin/status', { timeout: 20000 })
export const getLogs = (n = 200, level = '') => api.get('/api/admin/logs', { params: { n, level }, timeout: 10000 })
export const restartBackend = () => api.post('/api/admin/restart', {}, { timeout: 10000 })
export const installSentiment = () => api.post('/api/admin/install/sentiment', {}, { timeout: 15000 })
export const getPackageGroups = () => api.get('/api/admin/install/packages')
export const installPackages = (group) => api.post('/api/admin/install/packages', { group }, { timeout: 15000 })

// 원격 접속(stock.webig.ai) — 화면 빌드 · Cloudflare 터널 커넥터
export const getRemoteStatus = (check = false) => api.get('/api/admin/remote/status', { params: check ? { check: 1 } : {}, timeout: 30000 })
export const startTunnel = () => api.post('/api/admin/remote/tunnel/start', {}, { timeout: 30000 })
export const stopTunnel = () => api.post('/api/admin/remote/tunnel/stop', {}, { timeout: 20000 })
export const buildWeb = () => api.post('/api/admin/web/build', {}, { timeout: 15000 })
export const getBuildStatus = () => api.get('/api/admin/web/build', { timeout: 10000 })
export const getOpsMetrics = (days = 7) => api.get('/api/admin/metrics', { params: { days }, timeout: 30000 })
export const startSelftest = () => api.post('/api/admin/selftest', {}, { timeout: 60000 })
export const getSelftest = () => api.get('/api/admin/selftest', { timeout: 15000 })
