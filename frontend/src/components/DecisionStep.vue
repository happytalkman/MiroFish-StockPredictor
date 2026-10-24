<template>
  <div class="dec-step">
    <div v-if="loading && !pm.show" class="dec-loading">
      <div class="spinner"></div>
      <div>
        <div class="t">1~4단계 근거로 판단하는 중…</div>
        <div class="s">투자위원회(강세·약세·리스크·의장)가 사실표로 토론하는 동안 Grok 이 매매 계획을 세우고, JEV(typesafe-ai/jev)가 방향·전략·기술적 명제·CQ·ADR·위원회 쟁점의 확률을 독립적으로 채점합니다. 1~3분 걸립니다.</div>
      </div>
    </div>
    <ProgressModal
      :show="pm.show"
      title="5단계 판단 진행 중"
      subtitle="투자위원회가 사실표로 토론하는 동안 Grok 이 매매 계획을 세우고, JEV(TypeSafe)가 방향·전략·기술적 명제·CQ·ADR·위원회 쟁점 확률을 독립 채점합니다."
      done-title="판단이 완료되었습니다"
      :stages="pm.stages" :progress="pm.progress" :message="pm.message" :logs="pm.logs"
      :status="pm.status" :error="pm.error" :started-at="pm.startedAt" :expected-sec="expectedSec"
      @close="pmClose" @retry="pmClose(); lastAction === 'debate' ? rerunDebate(lastRounds) : run(true)"
    />
    <div v-if="error" class="dec-error">{{ error }} <button class="btn ghost sm" @click="run(true)">재시도</button></div>

    <template v-if="d">
      <!-- 헤드라인 -->
      <div class="hero" :class="'v-' + d.verdict_key">
        <div class="hero-left">
          <div class="cap">최종 판단 · {{ d.company_name }} ({{ d.ticker }})</div>
          <div class="verdict">{{ d.verdict }} <span class="vp">{{ d.verdict_pct }}%</span></div>
          <div class="headline">{{ d.headline }}</div>
          <div class="engines">
            <span class="eng" :class="{ ok: d.engines.jev.ok }">JEV {{ d.engines.jev.ok ? (d.engines.jev.model + ' · ' + (d.engines.jev.via === 'typesafe' ? 'TypeSafe NOUL' : 'Vercel Gateway')) : '실패' }}</span>
            <span class="eng" :class="{ ok: d.engines.grok.ok }">Grok {{ d.engines.grok.model || '실패' }}</span>
            <span class="eng" :class="{ ok: d.kg_used }">지식그래프 {{ d.kg_used ? '반영' : '없음' }}</span>
            <span v-if="d.engines.committee" class="eng" :class="{ ok: d.engines.committee.ok }">투자위원회 {{ d.engines.committee.ok ? ((d.engines.committee.rounds || '-') + '라운드' + (d.engines.committee.cached ? ' · 저장본' : '')) : '실패' }}</span>
            <span v-if="d.agreement" class="eng agree" :class="'ag-' + d.agreement.status" :title="d.agreement.note">위원회 {{ d.committee?.consensus_label || '' }} · 판단과 {{ d.agreement.label }}</span>
          </div>
          <div v-if="!d.engines.jev.ok" class="jev-err">JEV 오류: {{ d.engines.jev.error }} — 확률은 Grok 추정치로 대체했습니다.</div>
          <div v-if="d.engines.committee && !d.engines.committee.ok && d.engines.committee.error" class="jev-err">투자위원회: {{ d.engines.committee.error }}</div>
        </div>
        <div class="hero-kpis">
          <div class="kpi"><span>매수 쪽</span><b class="up">{{ d.buy_side_pct }}%</b></div>
          <div class="kpi"><span>관망</span><b class="flat">{{ d.hold_pct }}%</b></div>
          <div class="kpi"><span>매도 쪽</span><b class="down">{{ d.sell_side_pct }}%</b></div>
          <div class="kpi"><span>기술적 종합</span><b>{{ fmt(d.technical_score_pct) }}</b></div>
          <div class="kpi"><span>위험도</span><b :class="riskClass">{{ fmt(d.risk_pct) }}</b></div>
          <div class="kpi"><span>근거 강도</span><b>{{ fmt(d.conviction_pct) }}</b></div>
        </div>
      </div>

      <!-- 투자위원회 토론 -->
      <CommitteeDebate v-if="committeeView" :report-id="reportId" :committee="committeeView" :busy="loading" @rerun="rerunDebate" />
      <div v-else class="card cd-empty">
        <div>
          <div class="card-title">투자위원회 토론</div>
          <p class="muted small">이 판단에는 투자위원회 토론 결과가 없습니다. 강세·약세 애널리스트, 리스크 매니저, 의장이 번호 사실표만으로 토론하고, 그 요지와 쟁점을 JEV 가 함께 채점합니다.</p>
        </div>
        <button class="btn ghost" :disabled="loading" @click="rerunDebate(2)">⚖ 토론 열기</button>
      </div>

      <div class="grid2">
        <!-- 방향 확률 -->
        <div class="card">
          <div class="card-title">향후 20거래일 방향 확률 <span class="src">{{ srcLabel(d.direction_source) }}<template v-if="d.direction_confidence_pct != null"> · 신뢰 {{ d.direction_confidence_pct }}%</template></span></div>
          <div v-for="(v, k) in d.direction_probs" :key="k" class="bar-row">
            <span class="bl">{{ k }}</span>
            <div class="bar"><div class="fill" :class="dirClass(k)" :style="{ width: v + '%' }"></div></div>
            <span class="bv">{{ v }}%</span>
          </div>
        </div>
        <!-- 전략 확률 -->
        <div class="card">
          <div class="card-title">매매 전략 확률 <span class="src">{{ srcLabel(d.strategy_source) }}<template v-if="d.strategy_confidence_pct != null"> · 신뢰 {{ d.strategy_confidence_pct }}%</template></span></div>
          <div v-for="(v, k) in d.strategy_probs" :key="k" class="bar-row">
            <span class="bl">{{ k }}</span>
            <div class="bar"><div class="fill" :class="stratClass(k)" :style="{ width: v + '%' }"></div></div>
            <span class="bv" :class="{ best: k === d.verdict }">{{ v }}%</span>
          </div>
        </div>
      </div>

      <!-- 과거 성적 기반 보정 (예측 원장) -->
      <div v-if="d.calibration?.blend" class="card cal">
        <div class="card-title">과거 성적 기반 보정 확률
          <span class="src">채점된 예측 {{ d.calibration.n_scored }}건<template v-if="d.calibration.n_scored < d.calibration.min_fit"> · {{ d.calibration.min_fit }}건부터 학습 (지금은 과신 완화만)</template><template v-else> · 학습 엔진 {{ d.calibration.learned.length }}개</template></span>
        </div>
        <div class="cal-grid">
          <div>
            <div v-for="(v, k) in d.calibration.blend" :key="k" class="bar-row">
              <span class="bl">{{ k }}</span>
              <div class="bar"><div class="fill" :class="dirClass(k)" :style="{ width: v + '%' }"></div></div>
              <span class="bv" :class="{ best: k === d.calibration.blend_direction }">{{ v }}%</span>
            </div>
            <p class="cal-note">엔진별 확률을 과거 채점 결과로 보정(온도 스케일링)한 뒤 가중 평균했습니다. 원래 JEV 확률은 위 카드에 그대로 둡니다.</p>
          </div>
          <table class="eng">
            <thead><tr><th>엔진</th><th>원래 상승·보합·하락</th><th>보정 후</th><th>가중치</th><th>과거 적중</th></tr></thead>
            <tbody>
              <tr v-for="(p, k) in d.calibration.engines" :key="k">
                <td class="tl">{{ d.calibration.labels?.[k] || k }}</td>
                <td class="mono">{{ triple(d.calibration.raw?.[k]) }}</td>
                <td class="mono">{{ triple(p) }}</td>
                <td class="mono">{{ d.calibration.weights?.[k] != null ? Math.round(d.calibration.weights[k] * 100) + '%' : '-' }}</td>
                <td class="mono muted">{{ d.calibration.hit_rates?.[k] != null ? d.calibration.hit_rates[k] + '% (' + d.calibration.n_by_engine[k] + ')' : '기록 없음' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="d.calibration.strategy" class="cal-strat">
          <span class="muted small">전략 확률(과신 완화):</span>
          <span v-for="(v, k) in d.calibration.strategy" :key="k" class="chip" :class="{ best: k === d.verdict }">{{ k }} {{ v }}%</span>
        </div>
      </div>

      <!-- 몬테카를로 -->
      <div v-if="d.monte_carlo" class="card mc">
        <div class="card-title">몬테카를로 주가 경로 <span class="src">{{ d.monte_carlo.paths?.toLocaleString() }}개 경로 · {{ d.monte_carlo.horizon_days }}거래일 · {{ d.monte_carlo.method_label || '블록 부트스트랩' }}</span></div>
        <div class="plan-grid">
          <div class="pk"><span>상승(+3%↑) 확률</span><b class="up">{{ fmt(d.monte_carlo.direction_probs?.['상승']) }}</b></div>
          <div class="pk"><span>보합 확률</span><b>{{ fmt(d.monte_carlo.direction_probs?.['보합']) }}</b></div>
          <div class="pk"><span>하락(−3%↓) 확률</span><b class="down">{{ fmt(d.monte_carlo.direction_probs?.['하락']) }}</b></div>
          <div class="pk"><span>기간 수익률 중앙값</span><b>{{ signed(d.monte_carlo.return_pct?.p50) }}</b></div>
          <div class="pk"><span>5%·95% 범위</span><b>{{ signed(d.monte_carlo.return_pct?.p5) }} ~ {{ signed(d.monte_carlo.return_pct?.p95) }}</b></div>
          <div class="pk"><span>VaR 95% (기간)</span><b class="down">{{ signed(d.monte_carlo.var95_pct) }}</b></div>
          <template v-if="d.monte_carlo.plan">
            <div class="pk"><span>목표가 먼저 도달</span><b class="up">{{ fmt(d.monte_carlo.plan.p_target_first) }}</b></div>
            <div class="pk"><span>손절가 먼저 도달</span><b class="down">{{ fmt(d.monte_carlo.plan.p_stop_first) }}</b></div>
            <div class="pk"><span>기대 손익비(R)</span><b>{{ d.monte_carlo.plan.expected_r != null ? d.monte_carlo.plan.expected_r : '-' }}</b></div>
          </template>
        </div>
        <div v-if="fan" class="fan-wrap" @mouseleave="fanHover = null">
          <svg :viewBox="`0 0 ${FW} ${FH}`" class="fan" role="img" :aria-label="`거래일별 누적 수익률 분위 — 중앙값 ${signed(d.monte_carlo.return_pct?.p50)}`">
            <g v-for="t in fan.ticks" :key="'t' + t">
              <line :x1="FP.l" :x2="FW - FP.r" :y1="fan.y(t)" :y2="fan.y(t)" :stroke="t === 0 ? '#4b5563' : '#1e2533'" :stroke-width="t === 0 ? 1.2 : 1" />
              <text :x="FP.l - 6" :y="fan.y(t) + 3.5" text-anchor="end" class="fan-ax">{{ t > 0 ? '+' : '' }}{{ t }}%</text>
            </g>
            <text v-for="x in [0, 5, 10, 15, 20].filter(v => v <= fan.n)" :key="'x' + x" :x="fan.x(x)" :y="FH - 6" text-anchor="middle" class="fan-ax">{{ x }}일</text>
            <path :d="fan.band('p5', 'p95')" fill="#d55181" fill-opacity="0.16" />
            <path :d="fan.band('p25', 'p75')" fill="#d55181" fill-opacity="0.32" />
            <path :d="fan.line('p50')" fill="none" stroke="#d55181" stroke-width="2" stroke-linejoin="round" />
            <g v-for="lv in fan.levels" :key="lv.label">
              <line :x1="FP.l" :x2="FW - FP.r" :y1="fan.y(lv.v)" :y2="fan.y(lv.v)" stroke="#8892a4" stroke-dasharray="4 4" stroke-width="1" />
              <text :x="FW - FP.r - 4" :y="fan.y(lv.v) - 4" text-anchor="end" class="fan-lv">{{ lv.label }} {{ lv.v > 0 ? '+' : '' }}{{ lv.v }}%</text>
            </g>
            <line v-if="fanHover" :x1="fan.x(fanHover.day)" :x2="fan.x(fanHover.day)" :y1="FP.t" :y2="FH - FP.b" stroke="#8892a4" stroke-width="1" />
            <circle v-if="fanHover" :cx="fan.x(fanHover.day)" :cy="fan.y(fanHover.p50)" r="4" fill="#d55181" stroke="#0f1419" stroke-width="2" />
            <rect v-for="p in fan.pts" :key="'h' + p.day" :x="fan.x(p.day) - fan.step / 2" :y="FP.t" :width="fan.step" :height="FH - FP.t - FP.b"
              fill="transparent" @mouseenter="fanHover = p" />
          </svg>
          <div v-if="fanHover && fanHover.day > 0" class="fan-tip" :style="{ left: `${(fan.x(fanHover.day) / FW) * 100}%` }">
            <b>{{ fanHover.day }}거래일 후</b><br />중앙값 {{ signed(fanHover.p50) }}<br />
            <span class="muted">50% 범위 {{ signed(fanHover.p25) }} ~ {{ signed(fanHover.p75) }}</span><br />
            <span class="muted">90% 범위 {{ signed(fanHover.p5) }} ~ {{ signed(fanHover.p95) }}</span>
          </div>
          <div class="fan-legend">
            <span><i class="sw" style="background: rgba(213, 81, 129, .16)"></i>90% 범위 (5~95%)</span>
            <span><i class="sw" style="background: rgba(213, 81, 129, .32)"></i>50% 범위</span>
            <span><i class="ln"></i>중앙값</span>
            <span v-if="fan.levels.length"><i class="dash"></i>매매계획 목표·손절</span>
          </div>
        </div>
        <p class="cal-note">과거 {{ d.monte_carlo.lookback_days }}거래일 일간 수익률을 {{ d.monte_carlo.block }}일 단위로 다시 뽑아 이어 붙인 경로입니다 (변동성 군집 보존). 과거 분포가 미래에도 비슷하다는 가정에 기댑니다.</p>
      </div>

      <!-- 매매 계획 -->
      <div class="card">
        <div class="card-title">매매 계획 <span class="src">기간 {{ d.plan.horizon || '-' }}</span></div>
        <div class="plan-grid">
          <div class="pk"><span>현재가</span><b>{{ won(d.plan.current_price) }}</b></div>
          <div class="pk"><span>진입가</span><b>{{ won(d.plan.entry_price) }}</b></div>
          <div class="pk"><span>목표가</span><b class="up">{{ won(d.plan.target_price) }} <small>{{ signed(d.plan.target_pct) }}</small></b></div>
          <div class="pk"><span>손절가</span><b class="down">{{ won(d.plan.stop_price) }} <small>{{ signed(d.plan.stop_pct) }}</small></b></div>
          <div class="pk"><span>권장 비중</span><b>{{ fmt(d.plan.position_pct) }}</b></div>
          <div class="pk"><span>승률</span><b>{{ fmt(d.plan.win_rate_pct) }}</b></div>
          <div class="pk"><span>기대수익률</span><b :class="(d.plan.expected_return_pct || 0) >= 0 ? 'up' : 'down'">{{ signed(d.plan.expected_return_pct) }}</b></div>
          <div class="pk"><span>손익비</span><b>{{ d.plan.risk_reward != null ? '1 : ' + d.plan.risk_reward : '-' }}</b></div>
          <div v-if="d.plan.long_target_price" class="pk"><span>12개월 목표(리포트)</span><b class="up">{{ won(d.plan.long_target_price) }}</b></div>
        </div>
        <p v-if="d.plan.fixes?.length" class="plan-fix">자동 보정: {{ d.plan.fixes.join(' · ') }}</p>
        <ol v-if="d.plan.execution?.length" class="exec">
          <li v-for="(s, i) in d.plan.execution" :key="i">{{ s }}</li>
        </ol>
      </div>

      <!-- 기술적 분석 -->
      <div class="card">
        <div class="card-title">기술적 분석 결론 <span class="src">JEV 참 확률 · Grok 점수</span></div>
        <table class="tech">
          <thead><tr><th>항목</th><th>검증 명제</th><th>JEV</th><th>Grok</th><th>근거</th></tr></thead>
          <tbody>
            <tr v-for="t in d.technical" :key="t.key">
              <td class="tl">{{ t.label }}</td>
              <td class="tp">{{ t.proposition }}</td>
              <td><span class="pill" :class="pillClass(t)">{{ fmt(t.jev_pct) }}</span></td>
              <td class="muted">{{ fmt(t.grok_pct) }}</td>
              <td class="tn">{{ t.note }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="grid2">
        <div class="card">
          <div class="card-title">판단 근거</div>
          <ul class="lst"><li v-for="(r, i) in d.reasons" :key="i">{{ r }}</li></ul>
          <div class="card-title mt">리스크</div>
          <ul class="lst risk"><li v-for="(r, i) in d.risks" :key="i">{{ r }}</li></ul>
        </div>
        <div class="card">
          <div class="card-title">4단계 지식그래프 검증 (JEV)</div>
          <div v-for="c in d.cq_checks" :key="c.id" class="chk">
            <span class="badge">{{ c.id }}</span><span class="cq">{{ c.question }}</span><b :class="probClass(c.jev_pct)">{{ fmt(c.jev_pct) }}</b>
          </div>
          <div v-for="a in d.adr_checks" :key="a.id" class="chk">
            <span class="badge adr">{{ a.id }}</span><span class="cq">{{ a.title }} <small class="muted">(작성 확신 {{ a.confidence }}%)</small></span><b :class="probClass(a.jev_pct)">{{ fmt(a.jev_pct) }}</b>
          </div>
          <p v-if="!d.cq_checks.length && !d.adr_checks.length" class="muted">지식그래프 검증 결과가 없습니다.</p>
          <p class="muted small">50% 미만 항목은 근거가 약합니다. 4단계에서 해당 CQ·ADR 을 보강·수정한 뒤 다시 판단하면 정확도가 올라갑니다.</p>
        </div>
      </div>

      <div class="disclaimer">⚠ AI 시뮬레이션·확률 모델 기반의 참고 정보이며 투자 권유가 아닙니다. 최종 투자 결정과 책임은 투자자 본인에게 있습니다.</div>

      <div class="step-nav">
        <button class="btn ghost" @click="$emit('back')">← 4단계 지식그래프</button>
        <div class="right">
          <span class="muted small">생성 {{ d.created_at?.replace('T', ' ') }}</span>
          <button class="btn ghost" @click="downloadJson">⬇ 판단 결과 JSON</button>
          <button class="btn primary" :disabled="loading" @click="run(true)">↻ 다시 판단</button>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api/index.js'
import { getDecision, makeDecision } from '../api/stock.js'
import ProgressModal from './ProgressModal.vue'
import CommitteeDebate from './CommitteeDebate.vue'
import { useProgress } from '../composables/useProgress.js'

const props = defineProps({ reportId: { type: String, required: true }, stock: { type: Object, default: null } })
defineEmits(['back'])

const d = ref(null)
const loading = ref(false)
const error = ref('')

const { pm, start: pmStart, log: pmLog, stage: pmStage, done: pmDone, fail: pmFail, close: pmClose, follow } = useProgress()
const DEC_STAGES = [
  { key: 'evidence', label: '1~4단계 근거 수집' },
  { key: 'committee', label: '투자위원회 토론' },
  { key: 'plan', label: 'Grok 매매 계획' },
  { key: 'jev', label: 'JEV 확률 채점' },
  { key: 'combine', label: '결합·% 산출' }
]
const ORDER = ['evidence', 'committee', 'plan', 'jev', 'combine']
const STAGE_MAP = { evidence: 'evidence', committee: 'committee', plan: 'plan', plan_done: 'jev', jev: 'jev', combine: 'combine', done: 'done' }
const SPEAKER = { bull: '강세', bear: '약세', risk: '리스크 매니저', moderator: '의장', 'bull+bear': '강세·약세' }

// ── 투자위원회 토론 API (api/stock.js 에 옮겨도 됨)
const getCommittee = (id) => api.get(`/api/stock/committee/${id}`, { timeout: 20000 })
const runCommittee = (id, rounds = 2, force = true) => api.post(`/api/stock/committee/${id}`, { force, rounds }, { timeout: 60000 })

const cm = ref(null)            // committee.json 전체 (발언 기록·사실표)
const expectedSec = ref(150)
const lastAction = ref('decide')
const lastRounds = ref(2)

async function loadCommittee() {
  try { const r = await getCommittee(props.reportId); cm.value = r.data || null } catch (e) { cm.value = null }
}

// 판단(JEV 쟁점 %) + 토론 기록을 합쳐 CommitteeDebate 에 전달
const committeeView = computed(() => {
  const s = d.value?.committee
  const full = cm.value
  if (!full && !s) return null
  const base = full ? { ...full } : {
    created_at: s.created_at, rounds_held: s.rounds, facts: [], transcript: [],
    final: {
      summary: s.summary, bull_score: s.bull_score, bear_score: s.bear_score,
      consensus: s.consensus, consensus_label: s.consensus_label,
      recommended_strategy: s.recommended_strategy, recommended_label: s.recommended_label,
      position_cap_pct: s.position_cap_pct, key_issues: s.key_issues || [], unresolved: s.unresolved || [],
      fact_corrections_count: s.corrections_count
    }
  }
  if (s) {
    // 판단 이후 토론이 다시 열렸다면 JEV % 는 이전 토론 기준이므로 숨김
    const same = !full || !s.created_at || full.created_at === s.created_at
    const jmap = Object.fromEntries((s.key_issues || []).map(k => [k.id, k.jev_bull_pct]))
    base.final = { ...(base.final || {}), key_issues: ((base.final || {}).key_issues || []).map(k => ({ ...k, jev_bull_pct: same ? (jmap[k.id] ?? null) : null })) }
    base.jev_bull_pct = same ? s.jev_bull_pct : null
    if (!same) base.stale_note = '이 토론은 현재 판단 이후에 다시 열렸습니다 — JEV 쟁점 확률을 보려면 "다시 판단"을 실행하세요.'
  } else if (full) {
    base.stale_note = '현재 판단에는 이 토론이 반영되지 않았습니다 — "다시 판단"을 실행하면 JEV 가 쟁점을 채점합니다.'
  }
  return base
})

function stageStatus(k) { return pm.stages.find(x => x.key === k)?.status }
function committeeNote(det) {
  if (!det || !det.round) return ''
  return `${det.round}라운드 · ${SPEAKER[det.speaker] || ''}`.trim()
}
function applyStage(t) {
  const det = t.progress_detail || {}
  // 병렬 단계: 백엔드가 위원회·계획 상태를 함께 보냄
  if (det.stages && typeof det.stages === 'object') {
    pmStage('evidence', 'done')
    Object.entries(det.stages).forEach(([k, v]) => {
      if (!ORDER.includes(k)) return
      pmStage(k, v, k === 'committee' ? (v === 'running' ? committeeNote(det) : '') : undefined)
    })
    return
  }
  const cur = STAGE_MAP[det.stage] || (t.progress < 12 ? 'evidence' : null)
  if (!cur) return
  const idx = cur === 'done' ? ORDER.length : ORDER.indexOf(cur)
  ORDER.forEach((k, i) => {
    if (i < idx && stageStatus(k) === 'error') return   // 위원회 실패 표시는 유지
    pmStage(k, i < idx ? 'done' : i === idx ? 'running' : 'pending', k === 'committee' && i === idx ? committeeNote(det) : undefined)
  })
}

// 판단 작업 (started=true 면 이미 열린 진행 팝업을 이어서 사용)
async function decisionTask(force, { map = (p) => p, started = false } = {}) {
  const r = await makeDecision(props.reportId, force)
  if (!r.data?.task_id) {
    d.value = r.data
    await loadCommittee()
    if (started) pmDone(`판단 완료 — ${r.data.verdict} ${r.data.verdict_pct}%`)
    return
  }
  if (!started) {
    pmStart(DEC_STAGES.map(x => ({ ...x, status: 'pending' })))
    pmLog('판단 작업을 시작합니다')
  }
  await follow(r.data.task_id, { map, onUpdate: applyStage })
  ORDER.forEach(k => { if (stageStatus(k) !== 'error') pmStage(k, 'done', k === 'committee' ? '' : undefined) })
  const g = await getDecision(props.reportId)
  d.value = g.data
  await loadCommittee()
  pmDone(`판단 완료 — ${g.data.verdict} ${g.data.verdict_pct}%`)
}

function failWith(e, fallback) {
  const msg = e.response?.data?.error || e.message || fallback
  if (pm.show) pmFail(msg)
  error.value = msg
}

async function run(force = false) {
  error.value = ''
  loading.value = true
  lastAction.value = 'decide'
  expectedSec.value = 150
  try {
    if (!force) {
      try {
        const r = await getDecision(props.reportId)
        d.value = r.data
        loadCommittee()
        return
      } catch (e) { /* 없으면 생성 */ }
    }
    await decisionTask(force)
  } catch (e) {
    failWith(e, '판단 실패')
  } finally { loading.value = false }
}

// "토론 다시 하기": 위원회를 강제로 다시 연 뒤(POST /committee force) 판단을 다시 실행 — 판단은 새 토론 저장본을 재사용
async function rerunDebate(rounds = 2) {
  if (loading.value) return
  error.value = ''
  loading.value = true
  lastAction.value = 'debate'
  lastRounds.value = rounds
  expectedSec.value = 60 + rounds * 45
  pmStart(DEC_STAGES.map(x => ({ ...x, status: 'pending' })))
  pmLog(`투자위원회 토론을 ${rounds}라운드로 다시 엽니다`)
  try {
    let taskId = null
    try {
      const c = await runCommittee(props.reportId, rounds, true)
      taskId = c.data?.task_id || null
    } catch (e) {
      if (e.response?.status !== 404) throw e
      pmLog('토론 전용 API 가 없어 판단만 다시 실행합니다 (저장된 토론 사용)', 'err')
    }
    if (taskId) {
      pmStage('evidence', 'done')
      pmStage('committee', 'running')
      await follow(taskId, {
        map: (p) => Math.round(p * 0.6),
        onUpdate: (t) => { const det = t.progress_detail || {}; if (det.round) pmStage('committee', 'running', committeeNote(det)) }
      })
      pmStage('committee', 'done', '')
      await loadCommittee()
      pmLog('토론 완료 — 새 토론으로 판단을 다시 실행합니다')
    }
    await decisionTask(true, { started: true, map: taskId ? (p) => 60 + Math.round(p * 0.4) : (p) => p })
  } catch (e) {
    failWith(e, '토론 실패')
  } finally { loading.value = false }
}
onMounted(() => run(false))

const fmt = (v) => (v == null ? '-' : `${Math.round(v * 10) / 10}%`)
const signed = (v) => (v == null ? '' : `${v > 0 ? '+' : ''}${v}%`)
const won = (v) => (v == null ? '-' : `${Math.round(v).toLocaleString('ko-KR')}원`)
const srcLabel = (s) => ({ jev: 'JEV 채점', grok: 'Grok 추정', agents: '에이전트 투표로 대체', prior: '정보 부족 · 균등' }[s] || 'Grok 추정')
const dirClass = (k) => ({ 상승: 'up', 보합: 'flat', 하락: 'down' }[k])
const stratClass = (k) => ({ 매수: 'up', 분할매수: 'up2', 관망: 'flat', 비중축소: 'down2', 매도: 'down' }[k])
const probClass = (p) => (p == null ? 'muted' : p >= 65 ? 'up' : p >= 50 ? 'flat' : 'down')
const pillClass = (t) => {
  const p = t.jev_pct
  if (p == null) return 'na'
  const bad = t.key === 'overbought'
  if (p >= 60) return bad ? 'down' : 'up'
  if (p >= 45) return 'flat'
  return bad ? 'up' : 'down'
}
const triple = (p) => p ? ['상승', '보합', '하락'].map(k => (p[k] ?? '-')).join(' · ') : '-'
// 몬테카를로 팬 차트 (거래일별 누적 수익률 분위)
const FW = 640, FH = 190
const FP = { l: 44, r: 12, t: 10, b: 22 }
const fanHover = ref(null)
const fan = computed(() => {
  const mc = d.value?.monte_carlo
  const rows = mc?.fan
  if (!Array.isArray(rows) || rows.length < 2) return null
  const pts = [{ day: 0, p5: 0, p25: 0, p50: 0, p75: 0, p95: 0 }, ...rows]
  const pl = d.value?.plan || {}
  const levels = []
  if (mc.plan && pl.current_price) {
    if (pl.target_price) levels.push({ label: '목표', v: Math.round((pl.target_price / pl.current_price - 1) * 1000) / 10 })
    if (pl.stop_price) levels.push({ label: '손절', v: Math.round((pl.stop_price / pl.current_price - 1) * 1000) / 10 })
  }
  let lo = Math.min(...pts.map(p => p.p5), ...levels.map(l => l.v)), hi = Math.max(...pts.map(p => p.p95), ...levels.map(l => l.v))
  const pad = (hi - lo) * 0.08 || 1
  lo -= pad; hi += pad
  const n = rows.length
  const x = (day) => FP.l + (day / n) * (FW - FP.l - FP.r)
  const y = (v) => FP.t + (1 - (v - lo) / (hi - lo)) * (FH - FP.t - FP.b)
  const span = hi - lo
  const stepT = span > 60 ? 20 : span > 30 ? 10 : span > 12 ? 5 : 2
  const ticks = []
  for (let t = Math.ceil(lo / stepT) * stepT; t <= hi; t += stepT) ticks.push(t)
  const line = (k) => pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.day).toFixed(1)},${y(p[k]).toFixed(1)}`).join('')
  const band = (a, b) => line(b) + pts.slice().reverse().map(p => `L${x(p.day).toFixed(1)},${y(p[a]).toFixed(1)}`).join('') + 'Z'
  return { pts, n, x, y, ticks, line, band, levels, step: (FW - FP.l - FP.r) / n }
})
const riskClass = computed(() => { const r = d.value?.risk_pct; return r == null ? '' : r >= 66 ? 'down' : r >= 40 ? 'flat' : 'up' })

function downloadJson() {
  const blob = new Blob([JSON.stringify(d.value, null, 2)], { type: 'application/json' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${d.value.ticker}_판단_${props.reportId}.json`
  a.click()
  setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}
</script>

<style scoped>
.dec-step { display: flex; flex-direction: column; gap: 14px; }
.dec-loading { display: flex; gap: 16px; align-items: center; padding: 26px; background: #0f1419; border: 1px solid #2a3041; border-radius: 10px; }
.dec-loading .t { color: #e2e8f0; font-weight: 700; }
.dec-loading .s { color: #8892a4; font-size: 0.83rem; margin-top: 4px; line-height: 1.6; }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(59,130,246,.2); border-top-color: #3b82f6; border-radius: 50%; animation: spin .8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.dec-error { color: #fca5a5; background: rgba(239,68,68,.08); border: 1px solid rgba(239,68,68,.3); padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; }

.hero { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: 18px; padding: 22px 24px; border-radius: 12px; background: #0f1419; border: 1px solid #2a3041; border-left: 4px solid #f59e0b; }
.hero.v-buy, .hero.v-accumulate { border-left-color: #10b981; }
.hero.v-reduce, .hero.v-sell { border-left-color: #ef4444; }
.cap { font-size: 0.74rem; color: #8892a4; font-family: 'JetBrains Mono', monospace; }
.verdict { font-size: 2.1rem; font-weight: 800; color: #f59e0b; letter-spacing: -1px; margin-top: 4px; }
.v-buy .verdict, .v-accumulate .verdict { color: #10b981; }
.v-reduce .verdict, .v-sell .verdict { color: #ef4444; }
.vp { font-size: 1.1rem; color: #e2e8f0; margin-left: 6px; }
.headline { color: #e2e8f0; font-weight: 600; margin-top: 6px; }
.engines { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.eng { font-size: 0.7rem; padding: 3px 9px; border-radius: 999px; border: 1px solid #ef4444; color: #fca5a5; }
.eng.ok { border-color: #2a3041; color: #a0aec0; background: #151c27; }
.jev-err { margin-top: 8px; font-size: 0.74rem; color: #fca5a5; }
.eng.agree.ag-agree { border-color: rgba(16,185,129,.5); color: #6ee7b7; background: rgba(16,185,129,.08); }
.eng.agree.ag-partial { border-color: rgba(245,158,11,.5); color: #fcd34d; background: rgba(245,158,11,.08); }
.eng.agree.ag-disagree { border-color: rgba(239,68,68,.5); color: #fca5a5; background: rgba(239,68,68,.08); }
.cd-empty { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.cd-empty > div { flex: 1 1 260px; min-width: 0; }
.cd-empty .card-title { margin-bottom: 6px; }
.cd-empty p { margin: 0; }
.hero-kpis { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; align-content: center; }
.kpi { background: #0b0f14; border: 1px solid #1e2533; border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 4px; }
.kpi span { font-size: 0.7rem; color: #8892a4; }
.kpi b { font-size: 1.15rem; color: #e2e8f0; }

.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.cal-grid { display: grid; grid-template-columns: minmax(220px, 1fr) 1.6fr; gap: 18px; align-items: start; }
.cal-note { font-size: 0.74rem; color: #8892a4; margin: 6px 0 0; line-height: 1.5; }
.eng { width: 100%; border-collapse: collapse; font-size: 0.78rem; }
.eng th { text-align: left; color: #8892a4; font-weight: 600; padding: 4px 6px; border-bottom: 1px solid #2a3041; }
.eng td { padding: 5px 6px; border-bottom: 1px solid #1e2533; color: #c9d4e0; }
.eng .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-variant-numeric: tabular-nums; }
.cal-strat { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.cal-strat .chip { font-size: 0.74rem; padding: 2px 8px; border-radius: 99px; background: #1e2533; color: #c9d4e0; }
.cal-strat .chip.best { background: rgba(16, 185, 129, .16); color: #6ee7b7; }
.small { font-size: 0.74rem; }
@media (max-width: 900px) { .cal-grid { grid-template-columns: 1fr; } .eng { display: block; overflow-x: auto; } }
.card { background: #0f1419; border: 1px solid #2a3041; border-radius: 10px; padding: 16px 18px; }
.card-title { font-size: 0.86rem; font-weight: 700; color: #e2e8f0; margin-bottom: 12px; display: flex; justify-content: space-between; gap: 8px; }
.card-title.mt { margin-top: 16px; }
.src { font-size: 0.7rem; color: #8892a4; font-weight: 500; }
.bar-row { display: grid; grid-template-columns: 64px 1fr 56px; align-items: center; gap: 10px; margin-bottom: 9px; }
.bl { font-size: 0.82rem; color: #c9d4e0; }
.bar { height: 12px; background: #1e2533; border-radius: 999px; overflow: hidden; }
.fill { height: 100%; border-radius: 999px; transition: width .6s ease; background: #64748b; }
.fill.up { background: #10b981; } .fill.up2 { background: #34d399; } .fill.flat { background: #f59e0b; }
.fill.down2 { background: #f87171; } .fill.down { background: #ef4444; }
.bv { text-align: right; font-family: 'JetBrains Mono', monospace; font-size: 0.82rem; color: #e2e8f0; }
.bv.best { color: #fbbf24; font-weight: 700; }
.up { color: #10b981 !important; } .down { color: #ef4444 !important; } .flat { color: #f59e0b !important; }
.muted { color: #8892a4; } .small { font-size: 0.74rem; line-height: 1.6; }

.plan-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.pk { background: #0b0f14; border: 1px solid #1e2533; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 3px; }
.pk span { font-size: 0.7rem; color: #8892a4; }
.pk b { color: #e2e8f0; font-size: 1rem; }
.pk small { font-size: 0.74rem; }
.exec { margin: 12px 0 0; padding-left: 20px; color: #c9d4e0; font-size: 0.84rem; line-height: 1.8; }

.tech { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
.tech th { text-align: left; color: #8892a4; font-weight: 500; padding: 6px 8px; border-bottom: 1px solid #2a3041; font-size: 0.72rem; }
.tech td { padding: 8px; border-bottom: 1px solid #1e2533; color: #c9d4e0; vertical-align: top; }
.tl { font-weight: 700; color: #e2e8f0 !important; white-space: nowrap; }
.tp { color: #a0aec0 !important; }
.tn { color: #8892a4 !important; font-size: 0.75rem; }
.pill { display: inline-block; min-width: 52px; text-align: center; padding: 2px 8px; border-radius: 999px; font-family: 'JetBrains Mono', monospace; background: #1e2533; }
.pill.up { background: rgba(16,185,129,.15); } .pill.down { background: rgba(239,68,68,.15); } .pill.flat { background: rgba(245,158,11,.15); }

.lst { margin: 0; padding-left: 18px; color: #c9d4e0; font-size: 0.84rem; line-height: 1.75; }
.lst.risk li::marker { color: #ef4444; }
.chk { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; align-items: center; padding: 7px 0; border-bottom: 1px dashed #1e2533; font-size: 0.8rem; color: #c9d4e0; }
.badge { font-family: 'JetBrains Mono', monospace; font-size: 0.68rem; background: #1e2533; color: #93c5fd; padding: 2px 7px; border-radius: 4px; }
.badge.adr { color: #6ee7b7; }
.chk b { font-family: 'JetBrains Mono', monospace; }

.disclaimer { font-size: 0.76rem; color: #8892a4; padding: 10px 14px; border: 1px dashed #2a3041; border-radius: 8px; }
.step-nav { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
.step-nav .right { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.btn { border-radius: 8px; padding: 9px 16px; font-size: 0.84rem; font-weight: 600; cursor: pointer; font-family: inherit; border: 1px solid transparent; }
.btn:disabled { opacity: .4; cursor: not-allowed; }
.btn.primary { background: #3b82f6; color: #fff; }
.btn.ghost { background: transparent; border-color: #2a3041; color: #c9d4e0; }
.btn.sm { padding: 4px 10px; font-size: 0.75rem; }

@media (max-width: 860px) {
  .hero, .grid2 { grid-template-columns: 1fr; }
  .plan-grid { grid-template-columns: repeat(2, 1fr); }
  .tech .tp, .tech th:nth-child(2) { display: none; }
}
.plan-fix { margin: 8px 0 0; font-size: 11.5px; color: #fbbf24; background: rgba(245, 158, 11, .08); border-radius: 6px; padding: 6px 10px; }
.fan-wrap { position: relative; margin-top: 12px; }
.fan { width: 100%; height: auto; display: block; }
.fan-ax { font-size: 10px; fill: #8892a4; font-variant-numeric: tabular-nums; }
.fan-lv { font-size: 10px; fill: #c9d4e0; paint-order: stroke; stroke: #0f1419; stroke-width: 3px; }
.fan-tip { position: absolute; top: 4px; transform: translateX(-50%); pointer-events: none; background: #0b0f14; border: 1px solid #2a3041; border-radius: 6px; padding: 6px 9px; font-size: 0.72rem; color: #e2e8f0; white-space: nowrap; z-index: 3; }
.fan-legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 0.72rem; color: #c9d4e0; margin-top: 4px; }
.fan-legend span { display: inline-flex; align-items: center; gap: 6px; }
.fan-legend .sw { width: 12px; height: 10px; border-radius: 2px; display: inline-block; }
.fan-legend .ln { width: 14px; height: 2px; background: #d55181; display: inline-block; }
.fan-legend .dash { width: 14px; border-top: 1px dashed #8892a4; display: inline-block; }
</style>
