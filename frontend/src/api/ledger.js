import api from './index'

// 예측 원장 — 기록·채점·보정
export const getLedgerSummary = (ticker) => api.get('/api/ledger/summary', { params: ticker ? { ticker } : {}, timeout: 30000 })
export const getLedgerPredictions = (params = {}) => api.get('/api/ledger/predictions', { params, timeout: 30000 })
export const getLedgerPrediction = (id) => api.get(`/api/ledger/prediction/${id}`)
export const scoreLedger = (reportId) => api.post('/api/ledger/score', reportId ? { report_id: reportId } : {}, { timeout: 180000 })
export const backfillLedger = () => api.post('/api/ledger/backfill', {}, { timeout: 120000 })
