<template>
  <div class="stock-page">
    <!-- 네비게이션 바 -->
    <nav class="navbar">
      <div class="nav-left">
        <router-link to="/" class="nav-back">← 대시보드</router-link>
        <div class="nav-brand">
          <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-label="WeAidStock 로고">
            <circle cx="14" cy="14" r="13" stroke="#3b82f6" stroke-width="1.5"/>
            <path d="M6 14 C6 14, 10 8, 14 14 C18 20, 22 14, 22 14" stroke="#3b82f6" stroke-width="2" fill="none" stroke-linecap="round"/>
            <circle cx="21" cy="14" r="2" fill="#3b82f6"/>
          </svg>
          <span>WeAidStock 주식 예측</span>
        </div>
      </div>
      <div class="nav-right">
        <div class="nav-badge">AI 기반 한국 주식 분석</div>
        <router-link to="/reports" class="nav-back">보관함</router-link>
        <router-link to="/performance" class="nav-back">성과</router-link>
        <router-link to="/settings" class="nav-back">설정</router-link>
      </div>
    </nav>

    <div class="page-content" :class="{ wide: currentStep >= 2 }">
      <!-- 페이지 헤더 -->
      <div class="page-header">
        <h1 class="page-title">
          주식 예측 엔진
          <span class="title-accent">— AI Agent 시뮬레이션</span>
        </h1>
        <p class="page-desc">종목을 선택하고 WeAidStock AI가 뉴스, 재무 데이터, 지식 그래프를 분석하여 예측 보고서를 생성합니다.</p>
      </div>

      <!-- 스텝 인디케이터 -->
      <div class="step-indicator">
        <div
          v-for="(step, idx) in steps"
          :key="idx"
          class="step-item"
          :class="{
            'step-active': currentStep === idx,
            'step-done': currentStep > idx,
            'step-link': reportIdRef && idx >= 2
          }"
          @click="reportIdRef && idx >= 2 && (currentStep = idx)"
        >
          <div class="step-circle">
            <span v-if="currentStep > idx" class="step-check">✓</span>
            <span v-else>{{ idx + 1 }}</span>
          </div>
          <span class="step-label">{{ step }}</span>
          <div v-if="idx < steps.length - 1" class="step-line"></div>
        </div>
      </div>

      <!-- ==============================
           STEP 0: 종목 선택
           ============================== -->
      <div v-if="currentStep === 0" class="step-panel">
        <div class="panel-title">
          <span class="panel-num">01</span>
          종목 선택
        </div>

        <div class="search-box">
          <div class="search-input-wrapper">
            <span class="search-icon">🔍</span>
            <input
              v-model="searchQuery"
              type="text"
              class="search-input"
              placeholder="종목명 또는 종목코드 입력 (예: 삼성전자, 005930)"
              @input="onSearchInput"
              @keydown.enter="doSearch"
            />
            <button
              v-if="searchQuery"
              class="search-clear"
              @click="clearSearch"
            >×</button>
          </div>
          <button class="search-btn" @click="doSearch" :disabled="!searchQuery.trim()">
            검색
          </button>
        </div>

        <!-- 검색 중 -->
        <div v-if="searchLoading" class="search-loading">
          <div class="spinner-small"></div>
          <span>검색 중...</span>
        </div>

        <!-- 검색 결과 -->
        <div v-else-if="searchError" class="no-results" role="alert">
          <span>{{ searchError }}</span>
        </div>
        <div v-else-if="searchResults.length > 0" class="search-results">
          <div class="results-header">검색 결과 {{ searchResults.length }}건</div>
          <div
            v-for="item in searchResults"
            :key="item.ticker"
            class="result-item"
            :class="{ 'result-selected': selectedStock && selectedStock.ticker === item.ticker }"
            @click="selectStock(item)"
          >
            <div class="result-ticker">{{ item.ticker }}</div>
            <div class="result-name">{{ item.name }}</div>
            <div class="result-market">{{ item.market || 'KOSPI' }}</div>
            <div v-if="selectedStock && selectedStock.ticker === item.ticker" class="result-check">✓</div>
          </div>
        </div>

        <!-- 검색 결과 없음 -->
        <div v-else-if="searchQuery && !searchLoading && searchDone" class="no-results">
          <span>검색 결과가 없습니다.</span>
        </div>

        <!-- 선택된 종목 표시 -->
        <div v-if="selectedStock" class="selected-stock-card">
          <div class="selected-label">선택된 종목</div>
          <div class="selected-info">
            <div class="selected-ticker">{{ selectedStock.ticker }}</div>
            <div class="selected-name">{{ selectedStock.name }}</div>
          </div>
          <button class="next-btn" @click="proceedToAnalysis">
            분석 시작하기 →
          </button>
        </div>

        <!-- 검색어 없을 때 안내 -->
        <div v-if="!searchQuery && searchResults.length === 0" class="search-hint">
          <div class="hint-icon">📊</div>
          <div class="hint-text">
            <strong>KOSPI / KOSDAQ</strong> 상장 종목을 검색하세요<br/>
            <span>예시: 삼성전자, SK하이닉스, NAVER, 카카오</span>
          </div>
        </div>
      </div>

      <!-- ==============================
           STEP 1: 분석 & 시뮬레이션
           ============================== -->
      <div v-if="currentStep === 1" class="step-panel">
        <div class="panel-title">
          <span class="panel-num">02</span>
          분석 &amp; 시뮬레이션 진행 중
        </div>

        <!-- 선택 종목 요약 -->
        <div class="analysis-stock-badge">
          <span class="badge-ticker">{{ selectedStock?.ticker }}</span>
          <span class="badge-name">{{ selectedStock?.name }}</span>
        </div>

        <!-- 분석 단계 -->
        <div class="analysis-stages">
          <div
            v-for="(stage, idx) in analysisStages"
            :key="idx"
            class="analysis-stage"
            :class="{
              'stage-done': stage.status === 'done',
              'stage-running': stage.status === 'running',
              'stage-pending': stage.status === 'pending',
              'stage-error': stage.status === 'error'
            }"
          >
            <div class="stage-icon-wrap">
              <div v-if="stage.status === 'done'" class="stage-check-anim">✓</div>
              <div v-else-if="stage.status === 'running'" class="spinner-stage"></div>
              <div v-else-if="stage.status === 'error'" class="stage-error-icon">✗</div>
              <div v-else class="stage-pending-dot"></div>
            </div>
            <div class="stage-body">
              <div class="stage-title">{{ stage.label }}</div>
              <div v-if="stage.status === 'running'" class="stage-sub">진행 중...</div>
              <div v-else-if="stage.status === 'done'" class="stage-sub stage-done-text">완료</div>
              <div v-else-if="stage.status === 'error'" class="stage-sub stage-error-text">{{ stage.errorMsg || '오류 발생' }}</div>
              <div v-else class="stage-sub">대기 중</div>
              <!-- 라운드 정보 (시뮬레이션 단계) -->
              <div v-if="stage.key === 'simulation' && stage.status === 'running' && simRound > 0" class="round-badge">
                라운드 {{ simRound }} / {{ simTotalRounds || '?' }} 진행 중
              </div>
            </div>
          </div>
        </div>

        <!-- 에러 메시지 -->
        <div v-if="analysisError" class="error-card">
          <span class="error-icon">⚠</span>
          <div class="error-body">
            <div class="error-title">오류 발생</div>
            <div class="error-msg">{{ analysisError }}</div>
          </div>
          <button class="retry-btn" @click="resetAll">처음부터 다시 시작</button>
        </div>

        <!-- 2단계 리서치 (분석 완료 후 표시) -->
        <ResearchPanel
          v-if="analysisTaskId && researchInfo && !researchInfo.error"
          :task-id="analysisTaskId"
          class="step-research"
        />
      </div>

      <!-- ==============================
           STEP 2: 예측 보고서
           ============================== -->
      <div v-if="currentStep === 2" class="step-panel">
        <div class="panel-title">
          <span class="panel-num">03</span>
          예측 보고서
        </div>

        <!-- 증권사 리서치 리포트 (헤더·요약·차트·섹션·출처·면책 포함, PDF/Markdown 저장) -->
        <ResearchReport v-if="reportData" :report="reportData" :report-id="reportIdRef" />
        <template v-else>
          <div class="report-content" v-html="renderedReport"></div>
          <div class="disclaimer">
            <span class="disclaimer-icon">⚠</span>
            <p>
              본 보고서는 AI 에이전트 시뮬레이션 기반의 예측 결과이며, 투자 권유나 금융 조언이 아닙니다.
              실제 투자 결정은 전문가와 상담하시기 바랍니다. 과거 데이터와 AI 분석은 미래 수익을 보장하지 않습니다.
            </p>
          </div>
        </template>

        <details v-if="analysisTaskId" class="research-block">
          <summary>2단계 리서치 근거 · 뉴스·공시·해외·커뮤니티 (출처 링크)</summary>
          <ResearchPanel :task-id="analysisTaskId" />
        </details>

        <!-- 다시 시작 버튼 -->
        <div class="report-actions">
          <button class="reset-btn" @click="resetAll">새 종목 분석하기</button>
          <button class="next-btn" @click="currentStep = 3">4단계 지식그래프 →</button>
        </div>
      </div>

      <!-- 2단계 진행 팝업 -->
      <ProgressModal
        :show="pm.show"
        title="AI 종목 분석 진행 중"
        :subtitle="selectedStock ? `${selectedStock.name}(${selectedStock.ticker}) · 재무 → 뉴스·공시·해외 리서치 → 지식그래프 → 에이전트 시뮬레이션 → 보고서` : ''"
        done-title="예측 보고서가 완성되었습니다"
        :stages="pm.stages" :progress="pm.progress" :message="pm.message" :logs="pm.logs"
        :status="pm.status" :error="pm.error" :started-at="pm.startedAt" :expected-sec="300"
        @close="pmClose" @retry="retryAnalysis"
      />

      <!-- ── 4단계: 지식그래프 (Foundry 온톨로지) ── -->
      <div v-if="currentStep === 3 && reportIdRef" class="step-panel">
        <div class="panel-title">
          <span class="panel-num">04</span>
          지식그래프 · 의사결정 인사이트
        </div>
        <p class="step-desc">
          예측 보고서를 Palantir Foundry 온톨로지의 <b class="c-sem">Semantic</b> · <b class="c-kin">Kinetic</b> · <b class="c-dyn">Dynamic</b>
          3계층으로 나누고, 핵심 질문(CQ)과 의사결정 레코드(ADR)를 RDF/OWL 2 로 변환했습니다. 노드와 관계를 직접 추가·수정·삭제할 수 있습니다.
        </p>
        <KnowledgeGraphStep :report-id="reportIdRef" :stock="selectedStock" @back="currentStep = 2" @next="currentStep = 4" />
      </div>

      <!-- ── 5단계: 판단 ── -->
      <div v-if="currentStep === 4 && reportIdRef" class="step-panel">
        <div class="panel-title">
          <span class="panel-num">05</span>
          판단 · 매매전략과 기술적 분석
        </div>
        <p class="step-desc">
          1~4단계에서 모은 가격 지표·에이전트 투표·보고서·지식그래프(CQ·ADR)를 근거로 Grok 이 매매 계획을 세우고,
          JEV(TypeSafe)가 각 선택지와 명제의 확률을 독립적으로 채점해 % 로 제출합니다.
        </p>
        <DecisionStep :report-id="reportIdRef" :stock="selectedStock" @back="currentStep = 3" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import ProgressModal from '../components/ProgressModal.vue'
import { useProgress } from '../composables/useProgress.js'
import KnowledgeGraphStep from '../components/KnowledgeGraphStep.vue'
import DecisionStep from '../components/DecisionStep.vue'
import ResearchReport from '../components/ResearchReport.vue'
import ResearchPanel from '../components/ResearchPanel.vue'
import { reportMeta } from '../api/library.js'
import {
  searchStock,
  analyzeStock,
  getAnalyzeStatus,
  startSimulation,
  getSimulationStatus,
  generateReport,
  getReport,
  judgeReport,
  getTask
} from '../api/stock.js'

// ─── 상태 변수 ────────────────────────────────────────────────────────────────
const currentStep = ref(0)
const steps = ['종목 선택', '분석 & 시뮬레이션', '예측 보고서', '지식그래프', '판단']

// 검색
const searchQuery = ref('')
const searchResults = ref([])
const searchLoading = ref(false)
const searchDone = ref(false)
const searchError = ref('')
let searchTimer = null

// 선택 종목
const selectedStock = ref(null)

// 분석 단계
const analysisStages = ref([
  { key: 'financial', label: '재무·주가 데이터 수집 중', status: 'pending' },
  { key: 'news', label: '뉴스·공시·해외 리서치 중', status: 'pending' },
  { key: 'graph', label: '지식 그래프 구축 중', status: 'pending' },
  { key: 'simulation', label: '에이전트 시뮬레이션 중', status: 'pending' },
  { key: 'report', label: '보고서 생성 중', status: 'pending' }
])
const analysisError = ref('')
const simRound = ref(0)
const simTotalRounds = ref(0)

// 폴링 타이머
let analyzePoller = null
let simulationPoller = null
let reportPoller = null

// ID 추적
let currentTaskId = null
let currentSimId = null
let currentReportId = null
const reportIdRef = ref(null)
let simTaskId = null
let reportTaskId = null
const analysisTaskId = ref(null)     // 리서치 패널용 (분석 task_id = research.json 위치)
const researchInfo = ref(null)       // 분석 결과의 research 요약 {findings, sources, sentiment, engine} | {error} | null

// ─── 진행 팝업 ────────────────────────────────────────────────────────────────
const { pm, start: pmStart, log: pmLog, stage: pmStage, done: pmDone, fail: pmFail, close: pmClose } = useProgress()
const route = useRoute()
const STAGE_LABEL = { news: '뉴스·공시·해외 리서치', financial: '재무·주가 데이터', graph: '지식 그래프 구축', simulation: '에이전트 시뮬레이션', report: '예측 보고서 작성' }

async function taskInfo(id) {
  if (!id) return null
  try { const r = await getTask(id); return r.data || null } catch (e) { return null }
}

function retryAnalysis() {
  pmClose()
  startAnalysis()
}

// 보관함·성과 화면에서 ?report=<id>&step=2|3|4 로 들어오면 저장된 결과를 다시 엶 (재분석 없음)
const restoring = ref(false)
async function openSavedReport(reportId, step = 2) {
  restoring.value = true
  analysisError.value = ''
  try {
    const m = (await reportMeta(reportId)).data
    const res = await getReport(reportId)
    const data = res.data || res
    const content = data.markdown_content || data.content || ''
    selectedStock.value = { ticker: m.ticker, name: m.company_name, market: '' }
    searchQuery.value = m.company_name || m.ticker
    searchResults.value = [selectedStock.value]
    searchDone.value = true
    currentReportId = reportId
    reportIdRef.value = reportId
    currentSimId = m.simulation_id || null
    analysisTaskId.value = m.task_id || null
    reportContent.value = content
    reportData.value = data
    reportDate.value = (m.created_at || '').slice(0, 10)
    predictionSummary.value = parsePredictionSummary(res, content)
    analysisStages.value.forEach(st => { st.status = 'done'; st.errorMsg = '' })
    const want = Number(step)
    currentStep.value = want === 3 && m.has_kg !== undefined ? 3 : want === 4 ? 4 : 2
  } catch (e) {
    analysisError.value = `저장된 보고서를 열지 못했습니다: ${e?.response?.data?.error || e.message}`
  } finally { restoring.value = false }
}

onMounted(() => {
  const q = route.query || {}
  if (q.report && /^stock_report_[0-9a-f]{6,32}$/.test(String(q.report))) {
    openSavedReport(String(q.report), q.step || 2)
    return
  }
  if (q.ticker) {
    const item = { ticker: String(q.ticker), name: String(q.name || q.ticker), market: String(q.market || '') }
    selectedStock.value = item
    searchQuery.value = item.name
    searchResults.value = [item]
    searchDone.value = true
  }
})

// 보고서
const reportContent = ref('')
const reportDate = ref('')
const predictionSummary = ref(null)
const reportData = ref(null)

// ─── 계산된 속성 ─────────────────────────────────────────────────────────────
// ─── AI 판단 ──────────────────────────────────────────────────────────────────
const judgment = ref(null)
const judgmentLoading = ref(false)
const judgmentError = ref('')

const judgmentClass = computed(() => {
  const v = judgment.value?.verdict
  if (v === '매수' || v === '분할매수') return 'judge-buy'
  if (v === '매도' || v === '비중축소') return 'judge-sell'
  return judgment.value ? 'judge-hold' : ''
})

const judgmentSourceLabel = computed(() => {
  const j = judgment.value
  if (!j) return ''
  if (j.source === 'rule') return '규칙 기반'
  const via = j.source === 'vercel-gateway' ? ' · Gateway' : ''
  return (j.model || 'Grok') + via
})

async function runJudgment(force = false) {
  const reportId = currentReportId
  if (!reportId) return
  judgmentLoading.value = true
  judgmentError.value = ''
  try {
    const p = predictionSummary.value
    const prob = p?.probability ? parseFloat(String(p.probability).replace('%', '')) : null
    const res = await judgeReport(
      reportId,
      p ? { direction: p.direction, probability: isNaN(prob) ? null : prob } : null,
      force
    )
    if (reportId !== currentReportId) return
    judgment.value = res.data || null
  } catch (e) {
    judgmentError.value = e.response?.data?.error || e.message || '판단 생성 실패'
  } finally {
    judgmentLoading.value = false
  }
}

const predictionDirectionClass = computed(() => {
  if (!predictionSummary.value) return ''
  const dir = predictionSummary.value.direction
  if (dir === '상승') return 'direction-up'
  if (dir === '하락') return 'direction-down'
  return 'direction-neutral'
})

const predictionDirectionIcon = computed(() => {
  if (!predictionSummary.value) return ''
  const dir = predictionSummary.value.direction
  if (dir === '상승') return '▲'
  if (dir === '하락') return '▼'
  return '◆'
})

const renderedReport = computed(() => {
  if (!reportContent.value) return ''
  return simpleMarkdown(reportContent.value)
})

// ─── 마크다운 → HTML 변환 (경량) ─────────────────────────────────────────────
function simpleMarkdown(md) {
  let html = md
    // 코드 블록
    .replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${escHtml(code.trim())}</code></pre>`)
    // 인라인 코드
    .replace(/`([^`]+)`/g, (_, code) => `<code>${escHtml(code)}</code>`)
    // H1
    .replace(/^# (.+)$/gm, '<h1>$1</h1>')
    // H2
    .replace(/^## (.+)$/gm, '<h2>$1</h2>')
    // H3
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    // 굵게
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    // 기울임
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    // 수평선
    .replace(/^---+$/gm, '<hr/>')
    // 불릿 리스트
    .replace(/^\s*[-*] (.+)$/gm, '<li>$1</li>')
    // 숫자 리스트
    .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
    // 줄바꿈 → <br>
    .replace(/\n\n/g, '</p><p>')
    .replace(/\n/g, '<br/>')

  // <li> 묶기
  html = html.replace(/(<li>.*<\/li>)/gs, (match) => `<ul>${match}</ul>`)
  html = `<p>${html}</p>`

  // 중복 p 제거
  html = html
    .replace(/<p><h([1-3])>/g, '<h$1>')
    .replace(/<\/h([1-3])><\/p>/g, '</h$1>')
    .replace(/<p><pre>/g, '<pre>')
    .replace(/<\/pre><\/p>/g, '</pre>')
    .replace(/<p><hr\/><\/p>/g, '<hr/>')
    .replace(/<p><ul>/g, '<ul>')
    .replace(/<\/ul><\/p>/g, '</ul>')

  return html
}

function escHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// ─── 검색 ─────────────────────────────────────────────────────────────────────
function onSearchInput() {
  clearTimeout(searchTimer)
  if (!searchQuery.value.trim()) {
    searchResults.value = []
    searchDone.value = false
    return
  }
  searchTimer = setTimeout(() => {
    doSearch()
  }, 400)
}

async function doSearch() {
  if (!searchQuery.value.trim()) return
  searchLoading.value = true
  searchDone.value = false
  searchError.value = ''
  try {
    const res = await searchStock(searchQuery.value.trim())
    searchResults.value = res.data || res.results || []
    searchDone.value = true
  } catch (e) {
    console.error('검색 오류:', e)
    searchError.value = e.response?.data?.error || e.message || '종목 검색에 실패했습니다. 다시 시도해 주세요.'
    searchResults.value = []
    searchDone.value = true
  } finally {
    searchLoading.value = false
  }
}

function clearSearch() {
  searchError.value = ''
  searchQuery.value = ''
  searchResults.value = []
  searchDone.value = false
  selectedStock.value = null
}

function selectStock(item) {
  selectedStock.value = item
}

function proceedToAnalysis() {
  if (!selectedStock.value) return
  currentStep.value = 1
  startAnalysis()
}

// ─── 분석 플로우 ──────────────────────────────────────────────────────────────
function setStageStatus(key, status, errorMsg = '') {
  const stage = analysisStages.value.find(s => s.key === key)
  if (stage) {
    const prev = stage.status
    stage.status = status
    if (errorMsg) stage.errorMsg = errorMsg
    if (pm.show && prev !== status) {
      pmStage(key, status)
      const label = STAGE_LABEL[key] || key
      if (status === 'running') pmLog(`${label} 시작`)
      else if (status === 'done') pmLog(`${label} 완료`, 'ok')
      else if (status === 'error') pmFail(`${label} 실패: ${errorMsg || '알 수 없는 오류'}`)
    }
  }
}

async function startAnalysis() {
  analysisError.value = ''
  simRound.value = 0
  simTaskId = null
  reportTaskId = null
  pmStart(analysisStages.value.map(s => ({ key: s.key, label: STAGE_LABEL[s.key] || s.label, status: 'pending' })))
  pmLog(`${selectedStock.value?.name}(${selectedStock.value?.ticker}) 분석 작업을 요청합니다`)

  // 모든 단계 초기화
  analysisStages.value.forEach(s => {
    s.status = 'pending'
    s.errorMsg = ''
  })

  try {
    // 뉴스 & 재무 수집 (분석 시작 = 서버에서 한번에 처리)
    setStageStatus('news', 'running')
    setStageStatus('financial', 'running')

    const analyzeRes = await analyzeStock(
      selectedStock.value.ticker,
      selectedStock.value.name
    )
    const taskId = analyzeRes.task_id || analyzeRes.data?.task_id
    currentTaskId = taskId
    analysisTaskId.value = taskId
    researchInfo.value = null

    // 분석 상태 폴링
    pollAnalyzeStatus(taskId)
  } catch (e) {
    const msg = e.response?.data?.error || e.message || '분석 시작 실패'
    setStageStatus('news', 'error', msg)
    setStageStatus('financial', 'error', msg)
    analysisError.value = msg
  }
}

function pollAnalyzeStatus(taskId) {
  let retryCount = 0
  analyzePoller = setInterval(async () => {
    try {
      const res = await getAnalyzeStatus(taskId)
      const status = res.status || res.data?.status
      const detail = res.data?.progress_detail || {}
      const stage = detail.stage === 'research' ? 'news'
        : (typeof (res.data?.progress) === 'number' && res.data.progress < 36) ? 'financial' : ''
      const amsg = res.data?.message || res.message
      if (amsg) pmLog(amsg)
      const aprog = res.data?.progress ?? res.progress
      if (typeof aprog === 'number') pm.progress = Math.round(aprog * 0.45)

      // 뉴스/재무 단계 상태 업데이트
      if (stage === 'financial') {
        setStageStatus('financial', 'running')
      } else if (stage === 'news') {
        setStageStatus('financial', 'done')
        setStageStatus('news', 'running')
      } else if (stage === 'graph') {
        setStageStatus('news', 'done')
        setStageStatus('financial', 'done')
        setStageStatus('graph', 'running')
      }

      if (status === 'completed' || status === 'done') {
        clearInterval(analyzePoller)
        researchInfo.value = res.data?.result?.research ?? null
        setStageStatus('news', 'done')
        setStageStatus('financial', 'done')
        setStageStatus('graph', 'done')

        // 시뮬레이션 시작
        beginSimulation(taskId)
      } else if (status === 'failed' || status === 'error') {
        clearInterval(analyzePoller)
        const errMsg = res.error || '분석 실패'
        setStageStatus('news', 'error', errMsg)
        analysisError.value = errMsg
      }
      retryCount = 0
    } catch (e) {
      retryCount++
      if (retryCount >= 5) {
        clearInterval(analyzePoller)
        const msg = '분석 상태 확인 실패'
        analysisError.value = msg
        setStageStatus('news', 'error', msg)
      }
    }
  }, 3000)
}

async function beginSimulation(taskId) {
  try {
    setStageStatus('simulation', 'running')
    const simRes = await startSimulation(taskId)
    const simId = simRes.simulation_id || simRes.data?.simulation_id
    simTaskId = simRes.data?.sim_task_id || simRes.sim_task_id || null
    currentSimId = simId
    pollSimulationStatus(simId)
  } catch (e) {
    const msg = e.response?.data?.error || e.message || '시뮬레이션 시작 실패'
    setStageStatus('simulation', 'error', msg)
    analysisError.value = msg
  }
}

function pollSimulationStatus(simId) {
  let retryCount = 0
  if (simulationPoller) clearInterval(simulationPoller)
  simulationPoller = setInterval(async () => {
    try {
      const res = await getSimulationStatus(simId)
      const status = res.status || res.data?.status
      const round = res.current_round || res.data?.current_round || 0
      const total = res.total_rounds || res.data?.total_rounds || 0

      simRound.value = round
      simTotalRounds.value = total
      const st = await taskInfo(simTaskId)
      if (st) {
        if (st.message) pmLog(st.message)
        if (typeof st.progress === 'number') pm.progress = 45 + Math.round(st.progress * 0.2)
      }

      if (status === 'completed' || status === 'done') {
        clearInterval(simulationPoller)
        simulationPoller = null
        if (currentReportId) return
        setStageStatus('simulation', 'done')
        const mc = res.data?.monte_carlo
        if (mc?.direction_probs) {
          pmLog(`몬테카를로 ${Number(mc.paths).toLocaleString()}경로 · ${mc.horizon_days}거래일: 상승 ${mc.direction_probs['상승']}% · 보합 ${mc.direction_probs['보합']}% · 하락 ${mc.direction_probs['하락']}% · VaR95 ${mc.var95_pct}%`)
        }

        // 보고서 생성
        createReport(simId)
      } else if (status === 'failed' || status === 'error') {
        clearInterval(simulationPoller)
        const errMsg = res.error || '시뮬레이션 실패'
        setStageStatus('simulation', 'error', errMsg)
        analysisError.value = errMsg
      }
      retryCount = 0
    } catch (e) {
      retryCount++
      if (retryCount >= 5) {
        clearInterval(simulationPoller)
        const msg = '시뮬레이션 상태 확인 실패'
        analysisError.value = msg
        setStageStatus('simulation', 'error', msg)
      }
    }
  }, 4000)
}

async function createReport(simId) {
  try {
    setStageStatus('report', 'running')
    const repRes = await generateReport(simId)
    const reportId = repRes.report_id || repRes.data?.report_id
    if (!reportId) throw new Error('보고서 ID를 받지 못했습니다')
    reportTaskId = repRes.data?.task_id || repRes.task_id || null
    pmLog(repRes.data?.already_generated ? '이미 생성된 보고서를 불러옵니다' : 'Grok 이 8개 섹션의 리서치 리포트를 작성합니다')
    currentReportId = reportId
    reportIdRef.value = reportId
    pollReportStatus(reportId)
  } catch (e) {
    const msg = e.response?.data?.error || e.message || '보고서 생성 실패'
    setStageStatus('report', 'error', msg)
    analysisError.value = msg
  }
}

function pollReportStatus(reportId) {
  let retryCount = 0
  // 고정 시간으로 끊지 않고 서버 작업 상태를 따라감: 작업이 실패했거나, 진행 표시가 5분간 전혀 바뀌지 않거나, 25분이 넘으면 중단
  const t0 = Date.now()
  const STALL_MS = 5 * 60 * 1000
  const HARD_MS = 25 * 60 * 1000
  let lastSig = ''
  let lastChange = Date.now()
  if (reportPoller) clearInterval(reportPoller)
  const stopWith = (msg) => {
    clearInterval(reportPoller)
    reportPoller = null
    analysisError.value = msg
    setStageStatus('report', 'error', msg)
  }
  reportPoller = setInterval(async () => {
    const rt = await taskInfo(reportTaskId)
    if (rt) {
      if (rt.message) pmLog(rt.message)
      if (typeof rt.progress === 'number') pm.progress = 65 + Math.round(rt.progress * 0.35)
      const sig = `${rt.status}|${rt.progress}|${rt.message}`
      if (sig !== lastSig) { lastSig = sig; lastChange = Date.now() }
      if (rt.status === 'failed') return stopWith(rt.error || rt.message || '보고서 생성 실패')
    }
    try {
      const res = await getReport(reportId)
      const data = res.data || res
      const status = data.status
      const content = data.markdown_content || data.content || ''

      if (status === 'completed' || status === 'done' || (content && status !== 'failed')) {
        clearInterval(reportPoller)
        reportPoller = null
        setStageStatus('report', 'done')

        reportContent.value = content
        reportData.value = data
        reportDate.value = new Date().toLocaleDateString('ko-KR', {
          year: 'numeric', month: 'long', day: 'numeric'
        })

        // 예측 요약 파싱
        predictionSummary.value = parsePredictionSummary(res, content)

        // 보고서 단계로 이동
        currentStep.value = 2
        if (pm.show) pmDone('예측 보고서 생성 완료 — 3단계 결과 화면으로 이동합니다')

        // 판단은 5단계에서 1~4단계 근거를 모아 수행
      } else if (status === 'failed' || status === 'error') {
        clearInterval(reportPoller)
        reportPoller = null
        const errMsg = data.error || res.error || '보고서 생성 실패'
        setStageStatus('report', 'error', errMsg)
        analysisError.value = errMsg
      }
      retryCount = 0
    } catch (e) {
      // 보고서는 생성 완료 후에 저장되므로 생성 중에는 404가 정상 → 서버 작업이 살아 있는 동안 계속 대기
      if (e.response?.status === 404) {
        if (Date.now() - t0 > HARD_MS) return stopWith('보고서 생성 시간 초과 (25분)')
        if (Date.now() - lastChange > STALL_MS) return stopWith('보고서 작업이 5분간 응답이 없습니다 — 다시 시도해 주세요')
        return
      }
      if (++retryCount >= 5) stopWith('보고서 조회 실패')
    }
  }, 3000)
}

function parsePredictionSummary(res, content) {
  // API 응답에서 직접 가져오기
  const direct = res.prediction || res.data?.prediction
  if (direct) {
    return {
      direction: direct.direction || '분석 중',
      probability: direct.probability ? `${direct.probability}%` : '-',
      priceTarget: direct.price_target || null
    }
  }

  // 보고서 핵심 요약 표의 매수/매도/보합 비율로 방향 판단
  const pct = (label) => {
    const m = content.match(new RegExp(label + '\\s*\\|\\s*([\\d.]+)\\s*%'))
    return m ? parseFloat(m[1]) : null
  }
  const buy = pct('매수 비율')
  const sell = pct('매도 비율')
  const hold = pct('보합 비율')

  if (buy !== null && sell !== null) {
    let direction = '보합'
    if (buy - sell >= 10) direction = '상승'
    else if (sell - buy >= 10) direction = '하락'
    const top = direction === '상승' ? buy : direction === '하락' ? sell : (hold ?? Math.max(buy, sell))
    return { direction, probability: `${top.toFixed(1)}%`, priceTarget: null }
  }

  // 표가 없을 때: '에이전트 합의' 행 기준
  const consensus = (content.match(/에이전트 합의\s*\|\s*([^|\n]+)/) || [])[1] || ''
  let direction = '보합'
  if (/매수|상승|강세/.test(consensus)) direction = '상승'
  else if (/매도|하락|약세/.test(consensus)) direction = '하락'
  return { direction, probability: '-', priceTarget: null }
}

watch(analysisError, (v) => { if (v && pm.show && pm.status === 'running') pmFail(v) })

// ─── 리셋 ─────────────────────────────────────────────────────────────────────
function resetAll() {
  clearAllPollers()
  currentStep.value = 0
  selectedStock.value = null
  searchQuery.value = ''
  searchResults.value = []
  searchDone.value = false
  analysisError.value = ''
  simRound.value = 0
  simTotalRounds.value = 0
  reportContent.value = ''
  reportData.value = null
  predictionSummary.value = null
  judgment.value = null
  judgmentError.value = ''
  judgmentLoading.value = false
  currentTaskId = null
  currentSimId = null
  currentReportId = null
  reportIdRef.value = null
  analysisTaskId.value = null
  researchInfo.value = null
  analysisStages.value.forEach(s => {
    s.status = 'pending'
    s.errorMsg = ''
  })
}

function clearAllPollers() {
  if (analyzePoller) { clearInterval(analyzePoller); analyzePoller = null }
  if (simulationPoller) { clearInterval(simulationPoller); simulationPoller = null }
  if (reportPoller) { clearInterval(reportPoller); reportPoller = null }
}

onUnmounted(() => {
  clearAllPollers()
  clearTimeout(searchTimer)
})
</script>

<style scoped>
/* ─── 변수 ─────────────────────────────────────────────────────────────────── */
:root {
  --bg: #0f1419;
  --card: #1a1f2e;
  --card-border: #2a3041;
  --accent: #3b82f6;
  --accent-hover: #2563eb;
  --up: #10b981;
  --down: #ef4444;
  --neutral: #f59e0b;
  --text: #e2e8f0;
  --text-muted: #8892a4;
  --font: -apple-system, 'Apple SD Gothic Neo', 'Noto Sans KR', BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --mono: 'JetBrains Mono', 'Fira Code', monospace;
  --radius: 12px;
  --radius-sm: 8px;
}

/* ─── 기본 ─────────────────────────────────────────────────────────────────── */
.stock-page {
  min-height: 100vh;
  background: #0f1419;
  color: #e2e8f0;
  font-family: -apple-system, 'Apple SD Gothic Neo', 'Noto Sans KR', BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

/* ─── 네비게이션 ────────────────────────────────────────────────────────────── */
.navbar {
  height: 60px;
  background: #1a1f2e;
  border-bottom: 1px solid #2a3041;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 32px;
  position: sticky;
  top: 0;
  z-index: 100;
}

.nav-left {
  display: flex;
  align-items: center;
  gap: 24px;
}

.step-research { margin-top: 18px; }
.research-block { margin: 16px 0; border: 1px solid #2a3041; border-radius: 10px; background: #0b0f14; }
.research-block > summary { cursor: pointer; padding: 12px 16px; color: #e2e8f0; font-weight: 700; font-size: 0.88rem; }
.research-block[open] > summary { border-bottom: 1px solid #1e2533; }
.research-block > .rp { padding: 12px; }

.nav-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.nav-back {
  color: #8892a4;
  text-decoration: none;
  font-size: 0.85rem;
  transition: color 0.2s;
}

.nav-back:hover {
  color: #e2e8f0;
}

.nav-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 700;
  font-size: 1rem;
  color: #e2e8f0;
  letter-spacing: -0.3px;
}

.nav-badge {
  font-size: 0.75rem;
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.3);
  padding: 4px 12px;
  border-radius: 100px;
  font-family: 'JetBrains Mono', monospace;
  letter-spacing: 0.3px;
}

/* ─── 페이지 콘텐츠 ─────────────────────────────────────────────────────────── */
.page-content {
  max-width: 860px;
  margin: 0 auto;
  padding: 48px 24px 80px;
}

.page-content.wide {
  max-width: 1320px;
}

.step-link {
  cursor: pointer;
}

.step-desc {
  color: #8892a4;
  font-size: 0.86rem;
  line-height: 1.7;
  margin: -6px 0 18px;
}
.c-sem { color: #3b82f6; }
.c-kin { color: #f59e0b; }
.c-dyn { color: #10b981; }

.page-header {
  margin-bottom: 40px;
}

.page-title {
  font-size: 2rem;
  font-weight: 700;
  color: #e2e8f0;
  margin: 0 0 10px;
  letter-spacing: -0.5px;
}

.title-accent {
  font-size: 1.1rem;
  font-weight: 400;
  color: #8892a4;
  letter-spacing: 0;
}

.page-desc {
  color: #8892a4;
  font-size: 0.95rem;
  line-height: 1.6;
  margin: 0;
}

/* ─── 스텝 인디케이터 ───────────────────────────────────────────────────────── */
.step-indicator {
  display: flex;
  align-items: center;
  margin-bottom: 40px;
  gap: 0;
}

.step-item {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
}

.step-circle {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 2px solid #2a3041;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  font-weight: 700;
  color: #8892a4;
  background: #1a1f2e;
  flex-shrink: 0;
  transition: all 0.3s;
}

.step-active .step-circle {
  border-color: #3b82f6;
  color: #3b82f6;
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.15);
}

.step-done .step-circle {
  border-color: #10b981;
  background: #10b981;
  color: #fff;
}

.step-check {
  font-size: 0.85rem;
}

.step-label {
  font-size: 0.85rem;
  color: #8892a4;
  white-space: nowrap;
  transition: color 0.3s;
}

.step-active .step-label {
  color: #e2e8f0;
  font-weight: 600;
}

.step-done .step-label {
  color: #10b981;
}

.step-line {
  flex: 1;
  height: 1px;
  background: #2a3041;
  margin: 0 12px;
}

/* ─── 패널 공통 ─────────────────────────────────────────────────────────────── */
.step-panel {
  background: #1a1f2e;
  border: 1px solid #2a3041;
  border-radius: 12px;
  padding: 32px;
}

.panel-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 1.15rem;
  font-weight: 700;
  color: #e2e8f0;
  margin-bottom: 28px;
}

.panel-num {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.75rem;
  color: #3b82f6;
  background: rgba(59, 130, 246, 0.12);
  border: 1px solid rgba(59, 130, 246, 0.25);
  padding: 3px 8px;
  border-radius: 6px;
}

/* ─── 검색 ─────────────────────────────────────────────────────────────────── */
.search-box {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
}

.search-input-wrapper {
  flex: 1;
  position: relative;
  display: flex;
  align-items: center;
}

.search-icon {
  position: absolute;
  left: 14px;
  font-size: 1rem;
  pointer-events: none;
}

.search-input {
  width: 100%;
  background: #0f1419;
  border: 1px solid #2a3041;
  border-radius: 8px;
  padding: 13px 40px 13px 44px;
  color: #e2e8f0;
  font-size: 0.95rem;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s;
}

.search-input:focus {
  border-color: #3b82f6;
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

.search-input::placeholder {
  color: #4a5568;
}

.search-clear {
  position: absolute;
  right: 12px;
  background: none;
  border: none;
  color: #8892a4;
  cursor: pointer;
  font-size: 1.2rem;
  line-height: 1;
  padding: 0;
  transition: color 0.2s;
}

.search-clear:hover {
  color: #e2e8f0;
}

.search-btn {
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 13px 24px;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.2s;
  white-space: nowrap;
}

.search-btn:hover:not(:disabled) {
  background: #2563eb;
}

.search-btn:disabled {
  background: #2a3041;
  color: #4a5568;
  cursor: not-allowed;
}

/* 검색 로딩 */
.search-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #8892a4;
  font-size: 0.9rem;
  padding: 16px 0;
}

/* 검색 결과 */
.search-results {
  border: 1px solid #2a3041;
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 20px;
}

.results-header {
  padding: 10px 16px;
  background: #0f1419;
  font-size: 0.78rem;
  color: #8892a4;
  font-family: 'JetBrains Mono', monospace;
  border-bottom: 1px solid #2a3041;
}

.result-item {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 14px 16px;
  cursor: pointer;
  border-bottom: 1px solid #1e2535;
  transition: background 0.15s;
}

.result-item:last-child {
  border-bottom: none;
}

.result-item:hover {
  background: rgba(59, 130, 246, 0.06);
}

.result-selected {
  background: rgba(59, 130, 246, 0.1);
  border-left: 3px solid #3b82f6;
}

.result-ticker {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  font-weight: 700;
  color: #3b82f6;
  min-width: 72px;
}

.result-name {
  flex: 1;
  font-size: 0.95rem;
  color: #e2e8f0;
}

.result-market {
  font-size: 0.75rem;
  color: #8892a4;
  background: #2a3041;
  padding: 2px 8px;
  border-radius: 4px;
}

.result-check {
  color: #10b981;
  font-size: 1rem;
  font-weight: 700;
}

/* 검색 결과 없음 */
.no-results {
  text-align: center;
  color: #8892a4;
  padding: 28px;
  font-size: 0.9rem;
}

/* 선택된 종목 카드 */
.selected-stock-card {
  background: rgba(59, 130, 246, 0.08);
  border: 1px solid rgba(59, 130, 246, 0.3);
  border-radius: 10px;
  padding: 20px 24px;
  display: flex;
  align-items: center;
  gap: 20px;
  margin-top: 8px;
}

.selected-label {
  font-size: 0.75rem;
  color: #3b82f6;
  font-family: 'JetBrains Mono', monospace;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  white-space: nowrap;
}

.selected-info {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 12px;
}

.selected-ticker {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1rem;
  font-weight: 700;
  color: #3b82f6;
}

.selected-name {
  font-size: 1rem;
  color: #e2e8f0;
}

.next-btn {
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 8px;
  padding: 11px 22px;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: background 0.2s, transform 0.1s;
  white-space: nowrap;
}

.next-btn:hover {
  background: #2563eb;
  transform: translateX(2px);
}

/* 검색 힌트 */
.search-hint {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 28px 20px;
  border: 1px dashed #2a3041;
  border-radius: 8px;
  margin-top: 8px;
}

.hint-icon {
  font-size: 2rem;
  flex-shrink: 0;
}

.hint-text {
  font-size: 0.9rem;
  color: #8892a4;
  line-height: 1.7;
}

.hint-text strong {
  color: #e2e8f0;
}

/* ─── 분석 단계 ─────────────────────────────────────────────────────────────── */
.analysis-stock-badge {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  background: #0f1419;
  border: 1px solid #2a3041;
  border-radius: 8px;
  padding: 8px 16px;
  margin-bottom: 28px;
}

.badge-ticker {
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.85rem;
  color: #3b82f6;
  font-weight: 700;
}

.badge-name {
  font-size: 0.9rem;
  color: #e2e8f0;
}

.analysis-stages {
  display: flex;
  flex-direction: column;
  gap: 0;
  border: 1px solid #2a3041;
  border-radius: 10px;
  overflow: hidden;
}

.analysis-stage {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 18px 22px;
  border-bottom: 1px solid #1e2535;
  transition: background 0.3s;
}

.analysis-stage:last-child {
  border-bottom: none;
}

.analysis-stage.stage-running {
  background: rgba(59, 130, 246, 0.05);
}

.analysis-stage.stage-done {
  background: rgba(16, 185, 129, 0.04);
}

.analysis-stage.stage-error {
  background: rgba(239, 68, 68, 0.04);
}

.stage-icon-wrap {
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* 완료 체크 애니메이션 */
.stage-check-anim {
  width: 28px;
  height: 28px;
  background: #10b981;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 0.85rem;
  font-weight: 700;
  animation: popIn 0.35s cubic-bezier(0.175, 0.885, 0.32, 1.275);
}

@keyframes popIn {
  0% { transform: scale(0); opacity: 0; }
  100% { transform: scale(1); opacity: 1; }
}

/* 에러 아이콘 */
.stage-error-icon {
  width: 28px;
  height: 28px;
  background: #ef4444;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 0.85rem;
  font-weight: 700;
}

/* 대기 점 */
.stage-pending-dot {
  width: 12px;
  height: 12px;
  background: #2a3041;
  border-radius: 50%;
  margin: auto;
}

.stage-body {
  flex: 1;
}

.stage-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: #e2e8f0;
  margin-bottom: 3px;
}

.stage-pending .stage-title {
  color: #4a5568;
}

.stage-sub {
  font-size: 0.78rem;
  color: #8892a4;
  font-family: 'JetBrains Mono', monospace;
}

.stage-done-text {
  color: #10b981 !important;
}

.stage-error-text {
  color: #ef4444 !important;
}

/* 라운드 배지 */
.round-badge {
  display: inline-block;
  font-size: 0.75rem;
  background: rgba(59, 130, 246, 0.15);
  border: 1px solid rgba(59, 130, 246, 0.3);
  color: #3b82f6;
  padding: 2px 10px;
  border-radius: 100px;
  margin-top: 6px;
  font-family: 'JetBrains Mono', monospace;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

/* 에러 카드 */
.error-card {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.3);
  border-radius: 10px;
  padding: 20px;
  margin-top: 20px;
}

.error-icon {
  font-size: 1.3rem;
  flex-shrink: 0;
  color: #ef4444;
}

.error-body {
  flex: 1;
}

.error-title {
  font-size: 0.9rem;
  font-weight: 700;
  color: #ef4444;
  margin-bottom: 4px;
}

.error-msg {
  font-size: 0.85rem;
  color: #8892a4;
}

.retry-btn {
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.4);
  color: #ef4444;
  border-radius: 8px;
  padding: 8px 16px;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  white-space: nowrap;
  transition: background 0.2s;
}

.retry-btn:hover {
  background: rgba(239, 68, 68, 0.25);
}

/* ─── 보고서 ─────────────────────────────────────────────────────────────────── */
.report-header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 20px;
  border-bottom: 1px solid #2a3041;
}

.report-stock-info {
  display: flex;
  align-items: center;
  gap: 12px;
}

.report-ticker {
  font-family: 'JetBrains Mono', monospace;
  font-size: 1.1rem;
  font-weight: 700;
  color: #3b82f6;
}

.report-name {
  font-size: 1rem;
  color: #e2e8f0;
}

.report-date {
  font-size: 0.82rem;
  color: #8892a4;
}

/* 예측 + 판단 나란히 */
.verdict-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.3fr);
  gap: 16px;
  margin-bottom: 16px;
}

.card-caption {
  position: absolute;
  top: 10px;
  left: 24px;
  right: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.7rem;
  color: #8892a4;
  letter-spacing: 0.5px;
  font-family: 'JetBrains Mono', monospace;
}

/* 예측 요약 카드 */
.prediction-summary-card {
  position: relative;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 24px;
  background: #0f1419;
  border: 1px solid #2a3041;
  border-radius: 10px;
  padding: 34px 24px 20px;
}

/* AI 판단 카드 */
.judgment-card {
  position: relative;
  background: #0f1419;
  border: 1px solid #2a3041;
  border-left: 3px solid #3b82f6;
  border-radius: 10px;
  padding: 34px 24px 18px;
  min-height: 120px;
}
.judgment-card.judge-buy { border-left-color: #10b981; }
.judgment-card.judge-sell { border-left-color: #ef4444; }
.judgment-card.judge-hold { border-left-color: #f59e0b; }

.judge-source {
  margin-left: auto;
  padding: 2px 8px;
  border: 1px solid #2a3041;
  border-radius: 999px;
  font-size: 0.66rem;
  color: #a0aec0;
}
.judge-refresh {
  background: transparent;
  border: none;
  color: #8892a4;
  cursor: pointer;
  font-size: 0.95rem;
  padding: 0 2px;
}
.judge-refresh:hover { color: #e2e8f0; }

.judge-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #8892a4;
  font-size: 0.88rem;
  padding-top: 8px;
}
.judge-error {
  color: #fca5a5;
  font-size: 0.85rem;
  padding-top: 6px;
}
.judge-retry {
  margin-left: 8px;
  background: transparent;
  border: 1px solid #ef4444;
  color: #fca5a5;
  border-radius: 6px;
  padding: 2px 10px;
  cursor: pointer;
  font-family: inherit;
}

.judge-top {
  display: flex;
  align-items: center;
  gap: 18px;
}
.judge-verdict {
  font-size: 1.5rem;
  font-weight: 800;
  letter-spacing: -0.5px;
  color: #e2e8f0;
  white-space: nowrap;
}
.judge-buy .judge-verdict { color: #10b981; }
.judge-sell .judge-verdict { color: #ef4444; }
.judge-hold .judge-verdict { color: #f59e0b; }

.judge-meter { flex: 1; display: flex; flex-direction: column; gap: 4px; }
.judge-meter-bar {
  height: 6px;
  background: #1e2533;
  border-radius: 999px;
  overflow: hidden;
}
.judge-meter-fill {
  height: 100%;
  background: currentColor;
  color: #3b82f6;
  border-radius: 999px;
  transition: width 0.6s ease;
}
.judge-buy .judge-meter-fill { color: #10b981; }
.judge-sell .judge-meter-fill { color: #ef4444; }
.judge-hold .judge-meter-fill { color: #f59e0b; }
.judge-meter-label {
  font-size: 0.72rem;
  color: #8892a4;
  font-family: 'JetBrains Mono', monospace;
}

.judge-agree {
  margin-top: 10px;
  font-size: 0.8rem;
  color: #a0aec0;
}
.judge-agree.agree::before { content: '● '; color: #10b981; }
.judge-agree.disagree::before { content: '● '; color: #f59e0b; }

.judge-headline {
  margin-top: 8px;
  font-size: 0.95rem;
  font-weight: 600;
  color: #e2e8f0;
}
.judge-meta {
  margin-top: 6px;
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 0.76rem;
  color: #8892a4;
}

/* 판단 상세 */
.judgment-detail {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  background: #0f1419;
  border: 1px solid #2a3041;
  border-radius: 10px;
  padding: 18px 22px;
  margin-bottom: 28px;
}
.jd-title {
  font-size: 0.72rem;
  color: #8892a4;
  letter-spacing: 0.5px;
  font-family: 'JetBrains Mono', monospace;
  margin-bottom: 8px;
}
.jd-col ul {
  margin: 0;
  padding-left: 18px;
  color: #c9d4e0;
  font-size: 0.85rem;
  line-height: 1.6;
}
.jd-trigger {
  font-size: 0.85rem;
  color: #c9d4e0;
  line-height: 1.6;
  margin-bottom: 6px;
}
.jd-trigger b {
  display: inline-block;
  min-width: 36px;
  color: #8892a4;
  font-weight: 600;
  margin-right: 6px;
}

.prediction-direction {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 100px;
}

.direction-icon {
  font-size: 1.6rem;
}

.direction-label {
  font-size: 1.4rem;
  font-weight: 800;
  letter-spacing: -0.5px;
}

.direction-up .direction-icon,
.direction-up .direction-label {
  color: #10b981;
}

.direction-down .direction-icon,
.direction-down .direction-label {
  color: #ef4444;
}

.direction-neutral .direction-icon,
.direction-neutral .direction-label {
  color: #f59e0b;
}

.prediction-details {
  display: flex;
  gap: 32px;
}

.prediction-prob,
.prediction-target {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.prob-label,
.target-label {
  font-size: 0.75rem;
  color: #8892a4;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-family: 'JetBrains Mono', monospace;
}

.prob-value,
.target-value {
  font-size: 1.1rem;
  font-weight: 700;
  color: #e2e8f0;
}

/* 마크다운 보고서 */
.report-content {
  background: #0f1419;
  border: 1px solid #2a3041;
  border-radius: 10px;
  padding: 28px 32px;
  line-height: 1.8;
  color: #c9d4e0;
  font-size: 0.95rem;
  margin-bottom: 24px;
}

.report-content :deep(h1) {
  font-size: 1.5rem;
  font-weight: 700;
  color: #e2e8f0;
  margin: 0 0 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #2a3041;
}

.report-content :deep(h2) {
  font-size: 1.2rem;
  font-weight: 700;
  color: #e2e8f0;
  margin: 24px 0 12px;
}

.report-content :deep(h3) {
  font-size: 1rem;
  font-weight: 700;
  color: #a0aec0;
  margin: 20px 0 10px;
}

.report-content :deep(strong) {
  color: #e2e8f0;
}

.report-content :deep(ul) {
  padding-left: 24px;
  margin: 10px 0;
}

.report-content :deep(li) {
  margin: 6px 0;
}

.report-content :deep(hr) {
  border: none;
  border-top: 1px solid #2a3041;
  margin: 20px 0;
}

.report-content :deep(code) {
  background: #1e2535;
  color: #7dd3fc;
  padding: 2px 8px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', monospace;
  font-size: 0.88em;
}

.report-content :deep(pre) {
  background: #0a0e15;
  border: 1px solid #2a3041;
  border-radius: 8px;
  padding: 16px 20px;
  overflow-x: auto;
  margin: 16px 0;
}

.report-content :deep(pre code) {
  background: none;
  padding: 0;
  color: #a8d8ea;
}

/* 면책조항 */
.disclaimer {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  background: rgba(245, 158, 11, 0.06);
  border: 1px solid rgba(245, 158, 11, 0.2);
  border-radius: 10px;
  padding: 16px 20px;
  margin-bottom: 24px;
}

.disclaimer-icon {
  font-size: 1rem;
  color: #f59e0b;
  flex-shrink: 0;
  margin-top: 1px;
}

.disclaimer p {
  font-size: 0.82rem;
  color: #8892a4;
  line-height: 1.6;
  margin: 0;
}

/* 보고서 액션 */
.report-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

.reset-btn {
  background: transparent;
  border: 1px solid #3b82f6;
  color: #3b82f6;
  border-radius: 8px;
  padding: 11px 24px;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  font-family: inherit;
  transition: all 0.2s;
}

.reset-btn:hover {
  background: rgba(59, 130, 246, 0.1);
}

/* ─── 스피너 ─────────────────────────────────────────────────────────────────── */
.spinner-small {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(59, 130, 246, 0.2);
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

.spinner-stage {
  width: 22px;
  height: 22px;
  border: 2px solid rgba(59, 130, 246, 0.2);
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ─── 반응형 ─────────────────────────────────────────────────────────────────── */
@media (max-width: 640px) {
  .page-content {
    padding: 28px 16px 60px;
  }

  .step-panel {
    padding: 22px 18px;
  }

  .page-title {
    font-size: 1.5rem;
  }

  .title-accent {
    display: block;
    margin-top: 4px;
  }

  .search-box {
    flex-direction: column;
  }

  .search-btn {
    width: 100%;
  }

  .selected-stock-card {
    flex-direction: column;
    align-items: flex-start;
    gap: 14px;
  }

  .next-btn {
    width: 100%;
    text-align: center;
  }

  .step-label {
    display: none;
  }

  .report-header-bar {
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
  }

  .prediction-summary-card {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
  }

  .verdict-row,
  .judgment-detail {
    grid-template-columns: 1fr;
  }

  .report-content {
    padding: 18px 16px;
  }

  .navbar {
    padding: 0 16px;
  }

  .nav-badge {
    display: none;
  }
}
</style>
