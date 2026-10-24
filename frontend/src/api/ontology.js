import api from './index'

// 누적 온톨로지 — 전역 그래프 조회·검증·질의
export const getOntologyStats = () => api.get('/api/ontology/stats', { timeout: 30000 })
export const searchOntology = (q) => api.get('/api/ontology/search', { params: { q } })
export const getOntologyNode = (iri, depth = 1) => api.get('/api/ontology/node', { params: { iri, depth }, timeout: 30000 })
export const getOntologyHistory = (ticker) => api.get(`/api/ontology/history/${ticker}`)
export const backfillOntology = () => api.post('/api/ontology/backfill', {}, { timeout: 300000 })
export const validateOntology = () => api.get('/api/ontology/validate', { timeout: 120000 })
export const runSparql = (query) => api.post('/api/ontology/sparql', { query }, { timeout: 120000 })
export const ttlUrl = (shapes = false) => `/api/ontology/export.ttl${shapes ? '?shapes=1' : ''}`
