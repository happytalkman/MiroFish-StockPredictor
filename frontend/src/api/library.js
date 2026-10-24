import api from './index'

// 보관함
export const listReports = (params = {}) => api.get('/api/library/reports', { params, timeout: 30000 })
export const reportMeta = (id) => api.get(`/api/library/reports/${id}/meta`)
export const compareReports = (ids) => api.get('/api/library/compare', { params: { ids: ids.join(',') } })

// 관심종목
export const getWatchlist = () => api.get('/api/watchlist', { timeout: 30000 })
export const addWatch = (ticker, name, market = '') => api.post('/api/watchlist', { ticker, name, market })
export const removeWatch = (ticker) => api.delete(`/api/watchlist/${ticker}`)
export const runWatchlist = (tickers) => api.post('/api/watchlist/run', tickers ? { tickers } : {})
export const getWatchRuns = () => api.get('/api/watchlist/runs')

// 알림
export const getAlerts = (unread = false) => api.get('/api/alerts', { params: unread ? { unread: 1 } : {} })
export const readAlerts = (ids) => api.post('/api/alerts/read', ids ? { ids } : {})
export const checkAlerts = () => api.post('/api/alerts/check', {}, { timeout: 60000 })
