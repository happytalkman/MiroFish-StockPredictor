import api from './index'

// 종목 검색
export const searchStock = (query) => api.get('/api/stock/search', { params: { query } })

// 종목 분석 시작
export const analyzeStock = (ticker, companyName) => api.post('/api/stock/analyze', { ticker, company_name: companyName })

// 분석 상태 확인
export const getAnalyzeStatus = (taskId) => api.get(`/api/stock/analyze/${taskId}/status`)

// 시뮬레이션 시작
export const startSimulation = (taskId) => api.post('/api/stock/simulate', { task_id: taskId })

// 시뮬레이션 상태
export const getSimulationStatus = (simId) => api.get(`/api/stock/simulate/${simId}/status`)

// 보고서 생성
export const generateReport = (simId) => api.post(`/api/stock/report/${simId}`, {})

// 보고서 조회
export const getReport = (reportId) => api.get(`/api/stock/report/${reportId}`)

// 예측 → AI 판단 (xAI Grok)
export const judgeReport = (reportId, prediction, force = false) =>
  api.post(`/api/stock/judge/${reportId}`, { prediction, force }, { timeout: 120000 })

// ── 4단계: 지식그래프 (Foundry 온톨로지) ──
export const getKnowledgeGraph = (reportId) => api.get(`/api/stock/kg/${reportId}`)
export const generateKnowledgeGraph = (reportId, force = false) =>
  api.post(`/api/stock/kg/${reportId}`, { force }, { timeout: 60000 })  // 즉시 task_id 반환
export const saveKnowledgeGraph = (reportId, payload) => api.put(`/api/stock/kg/${reportId}`, payload)
export const regenerateInsight = (reportId) =>
  api.post(`/api/stock/kg/${reportId}/insight`, {}, { timeout: 60000 })
export const kgTtlUrl = (reportId) => `/api/stock/kg/${reportId}/ttl`

// 종합 의견 음성 (mp3 Blob)
export async function synthesizeSpeech(text, voice = 'eve', reportId = null) {
  const res = await fetch('/api/stock/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, voice, report_id: reportId })
  })
  if (!res.ok) {
    let msg = `음성 합성 실패 (${res.status})`
    try { msg = (await res.json()).error || msg } catch (e) { /* ignore */ }
    throw new Error(msg)
  }
  return await res.blob()
}

// ── 5단계: 판단 (Grok 계획 + JEV 확률) ──
export const getDecision = (reportId) => api.get(`/api/stock/decision/${reportId}`)
export const makeDecision = (reportId, force = false) =>
  api.post(`/api/stock/decision/${reportId}`, { force }, { timeout: 60000 })

// 백그라운드 작업 진행 상황 (분석·시뮬레이션·보고서·지식그래프·판단 공통)
export const getTask = (taskId) => api.get(`/api/stock/task/${taskId}`, { timeout: 15000 })
