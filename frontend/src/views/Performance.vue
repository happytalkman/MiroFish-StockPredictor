<template>
  <AppShell sub="예측 성과 · 확률 보정">
    <div class="t-intro">
      <div>
        <h1>예측 성과</h1>
        <p>5단계 판단이 끝날 때마다 엔진별 방향 확률과 매매계획을 기록하고, 기준일로부터 {{ th.dir_horizon || 20 }}거래일이 지나면 실제 주가로 채점합니다.
          상승은 {{ th.up_pct ?? 3 }}% 이상, 하락은 {{ th.down_pct ?? -3 }}% 이하, 그 사이는 보합입니다. 채점은 평일 16:40 에 자동으로도 돌아갑니다.</p>
      </div>
      <div class="t-actions">
        <input v-model.trim="ticker" class="t-input" placeholder="종목코드 필터 (예: 005930)" maxlength="6" aria-label="종목코드 필터" @keydown.enter="load" />
        <button class="t-btn sm" @click="load">적용</button>
        <button class="t-btn sm" :disabled="busy" @click="doBackfill">기존 판단 가져오기</button>
        <button class="t-btn sm primary" :disabled="busy" @click="doScore">{{ busy ? '채점 중…' : '지금 채점' }}</button>
      </div>
    </div>

    <div v-if="error" class="t-banner err">{{ error }}</div>
    <div v-if="note" class="t-banner ok">{{ note }}</div>

    <div class="t-kpis" v-if="sum">
      <div class="t-kpi"><span>기록된 예측</span><b>{{ sum.counts.total }}</b><small>진행 중 {{ (sum.counts.pending || 0) + (sum.counts.partial || 0) }}</small></div>
      <div class="t-kpi"><span>방향 채점 완료</span><b>{{ sum.n_scored }}</b><small>{{ sum.n_scored < sum.min_fit ? `${sum.min_fit}건부터 보정 학습` : '보정 학습 중' }}</small></div>
      <div class="t-kpi"><span>최종 전략 적중률</span><b>{{ pct(sum.strategy_hit_rate) }}</b><small>매수·분할매수=상승, 관망=보합</small></div>
      <div class="t-kpi"><span>위원회 권고 적중률</span><b>{{ pct(sum.committee_strategy_hit_rate) }}</b><small>투자위원회 권고 전략 기준</small></div>
      <div class="t-kpi"><span>계획 목표가 먼저 도달</span><b>{{ pct(sum.plan.target_pct) }}</b><small>손절 먼저 {{ pct(sum.plan.stop_pct) }} · 기간 만료 {{ pct(sum.plan.horizon_pct) }}</small></div>
      <div class="t-kpi"><span>계획 평균 손익비</span><b>{{ sum.plan.avg_r != null ? sum.plan.avg_r + 'R' : '-' }}</b><small>평균 수익률 {{ signed(sum.plan.avg_return_pct) }} · {{ sum.plan.n }}건</small></div>
    </div>

    <div class="t-grid2" v-if="sum">
      <div class="t-card">
        <div class="t-head"><h2>엔진별 성적</h2><span class="t-sub">Brier 낮을수록 좋음 · 기술점수 = 균등 예측 대비 개선</span></div>
        <div class="t-scroll">
          <table class="t-table">
            <thead><tr><th>엔진</th><th class="num">채점</th><th class="num">적중률</th><th class="num">Brier</th><th class="num">기술점수</th><th class="num">보정 T</th><th class="num">가중치</th></tr></thead>
            <tbody>
              <tr v-for="e in sum.engines" :key="e.engine">
                <td><span class="dot" :style="{ background: COLORS[e.engine] }"></span>{{ e.label }}</td>
                <td class="num">{{ e.n }}</td>
                <td class="num">{{ pct(e.hit_rate) }}</td>
                <td class="num">{{ e.brier ?? '-' }}</td>
                <td class="num" :class="e.skill_pct > 0 ? 't-good' : e.skill_pct < 0 ? 't-bad' : ''">{{ e.skill_pct != null ? signed(e.skill_pct) : '-' }}</td>
                <td class="num">{{ e.method === 'temperature' ? e.T : '과신 완화' }}</td>
                <td class="num">{{ Math.round((e.weight || 0) * 100) }}%</td>
              </tr>
              <tr v-if="!sum.engines.length"><td colspan="7" class="t-empty">아직 채점된 예측이 없습니다</td></tr>
            </tbody>
          </table>
        </div>
        <p class="t-small t-muted">보정 T 가 1보다 크면 그 엔진이 과신한다는 뜻입니다(확률을 완만하게 조정). 채점 {{ sum.min_fit }}건 전에는 균등 분포 5%를 섞어 극단값만 누그러뜨립니다.</p>
      </div>

      <div class="t-card rel-card">
        <div class="t-head">
          <h2>보정 곡선</h2>
          <button class="t-btn sm ghost" @click="showTable = !showTable">{{ showTable ? '그래프로 보기' : '표로 보기' }}</button>
        </div>
        <template v-if="relSeries.length && !showTable">
          <div class="rel-wrap" ref="relWrap">
            <svg :viewBox="`0 0 ${W} ${H}`" class="rel" role="img" aria-label="예측 확률 대비 실제 빈도">
              <g v-for="t in [0, 25, 50, 75, 100]" :key="'g' + t">
                <line :x1="x(0)" :x2="x(100)" :y1="y(t)" :y2="y(t)" class="grid" />
                <text :x="x(0) - 6" :y="y(t) + 3" class="tick" text-anchor="end">{{ t }}</text>
                <text :x="x(t)" :y="y(0) + 14" class="tick" text-anchor="middle">{{ t }}</text>
              </g>
              <line :x1="x(0)" :y1="y(0)" :x2="x(100)" :y2="y(100)" class="diag" />
              <text :x="x(100)" :y="y(100) - 6" class="tick" text-anchor="end">완벽한 보정</text>
              <text :x="(x(0) + x(100)) / 2" :y="H - 2" class="axis" text-anchor="middle">예측 확률 (%)</text>
              <text :x="10" :y="(y(0) + y(100)) / 2" class="axis" text-anchor="middle" :transform="`rotate(-90 10 ${(y(0) + y(100)) / 2})`">실제 빈도 (%)</text>
              <g v-for="s in relSeries" :key="s.engine">
                <polyline :points="s.points.map(p => `${x(p.pred)},${y(p.obs)}`).join(' ')" fill="none" :stroke="COLORS[s.engine]" stroke-width="2" stroke-linejoin="round" />
                <circle v-for="p in s.points" :key="s.engine + p.bin" :cx="x(p.pred)" :cy="y(p.obs)" r="4.5" :fill="COLORS[s.engine]" stroke="#0f1419" stroke-width="2"
                  @mouseenter="tip = { s, p, left: x(p.pred), top: y(p.obs) }" @mouseleave="tip = null" />
                <circle v-for="p in s.points" :key="'hit' + s.engine + p.bin" :cx="x(p.pred)" :cy="y(p.obs)" r="11" fill="transparent"
                  @mouseenter="tip = { s, p, left: x(p.pred), top: y(p.obs) }" @mouseleave="tip = null" />
              </g>
            </svg>
            <div v-if="tip" class="t-tooltip" :style="tipStyle">
              <b>{{ tip.s.label }}</b> · 구간 {{ tip.p.bin }}<br />예측 평균 {{ tip.p.pred }}% → 실제 {{ tip.p.obs }}% <span class="t-muted">({{ tip.p.n }}개)</span>
            </div>
          </div>
          <div class="t-legend"><span v-for="s in relSeries" :key="'l' + s.engine"><i :style="{ background: COLORS[s.engine] }"></i>{{ s.label }}</span></div>
        </template>
        <div v-else-if="relSeries.length" class="t-scroll">
          <table class="t-table">
            <thead><tr><th>엔진</th><th>구간</th><th class="num">개수</th><th class="num">예측 평균</th><th class="num">실제 빈도</th></tr></thead>
            <tbody>
              <template v-for="s in relSeries" :key="'t' + s.engine">
                <tr v-for="p in s.points" :key="'tr' + s.engine + p.bin"><td>{{ s.label }}</td><td>{{ p.bin }}</td><td class="num">{{ p.n }}</td><td class="num">{{ p.pred }}%</td><td class="num">{{ p.obs }}%</td></tr>
              </template>
            </tbody>
          </table>
        </div>
        <div v-else class="t-empty">채점된 예측이 쌓이면 엔진별로 "70%라고 한 것이 실제로 70% 일어났는지"를 보여 줍니다.</div>
      </div>
    </div>

    <div class="t-card" style="margin-top:14px">
      <div class="t-head">
        <h2>예측 기록</h2>
        <div class="t-actions">
          <select v-model="status" class="t-select" aria-label="상태 필터" @change="loadList">
            <option value="">전체</option><option value="pending">대기</option><option value="partial">진행 중</option><option value="scored">채점 완료</option>
          </select>
          <span class="t-sub">{{ list.length }}건</span>
        </div>
      </div>
      <div class="t-scroll">
        <table class="t-table">
          <thead>
            <tr><th>기준일</th><th>종목</th><th>전략</th><th class="num">기준가</th><th class="num">JEV 상승</th><th class="num">보정 상승</th>
              <th>상태</th><th class="num">경과</th><th class="num">수익률</th><th>실제</th><th>매매계획</th><th class="num">R</th></tr>
          </thead>
          <tbody>
            <tr v-for="p in list" :key="p.id" class="click" @click="openReport(p)" :title="'보고서 열기 · ' + p.id">
              <td class="t-mono">{{ p.as_of }}</td>
              <td>{{ p.company }} <span class="t-muted t-mono">{{ p.ticker }}</span></td>
              <td>{{ p.strategy || '-' }}</td>
              <td class="num">{{ won(p.base_price) }}</td>
              <td class="num">{{ p.sources?.jev ? p.sources.jev['상승'] + '%' : '-' }}</td>
              <td class="num">{{ p.calibrated ? p.calibrated['상승'] + '%' : '-' }}</td>
              <td><span class="t-pill" :class="p.status === 'scored' ? 'ok' : p.status === 'partial' ? 'warn' : ''"><i></i>{{ STATUS[p.status] || p.status }}</span></td>
              <td class="num">{{ p.days_elapsed != null ? p.days_elapsed + '일' : '-' }}</td>
              <td class="num" :class="retCls(p)">{{ signed(p.realized?.return_pct ?? p.mtm_pct) }}</td>
              <td>
                <span v-if="p.realized" :class="p.strategy_hit ? 't-good' : 't-bad'">{{ p.realized.class }} {{ p.strategy_hit ? '✓' : '✕' }}</span>
                <span v-else class="t-muted">-</span>
              </td>
              <td>{{ PLAN[p.plan_outcome?.outcome] || '-' }}<span v-if="p.plan_outcome?.day" class="t-muted"> · {{ p.plan_outcome.day }}일째</span></td>
              <td class="num">{{ p.plan_outcome?.r_multiple ?? '-' }}</td>
            </tr>
            <tr v-if="!list.length"><td colspan="12" class="t-empty">기록된 예측이 없습니다. 5단계 판단을 한 번 실행하거나 "기존 판단 가져오기"를 누르세요.</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </AppShell>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '../components/AppShell.vue'
import '../assets/terminal-ui.css'
import { getLedgerSummary, getLedgerPredictions, scoreLedger, backfillLedger } from '../api/ledger.js'

const router = useRouter()
// 범주형 팔레트(어두운 배경 검증 통과: 명도대·채도·색각 이상 구분·대비) — 엔진 고정 순서
const COLORS = { jev: '#3987e5', grok: '#d95926', committee: '#199e70', agents: '#c98500', mc: '#d55181', blend: '#9085e9' }
const STATUS = { pending: '대기', partial: '진행 중', scored: '채점 완료' }
const PLAN = { target: '목표 도달', stop: '손절', horizon: '기간 만료', open: '진행 중', 'n/a': '해당 없음' }
const sum = ref(null)
const list = ref([])
const ticker = ref('')
const status = ref('')
const busy = ref(false)
const error = ref('')
const note = ref('')
const showTable = ref(false)
const tip = ref(null)
const th = computed(() => sum.value?.thresholds || {})

const W = 520, H = 300, M = { l: 44, r: 14, t: 16, b: 34 }
const x = (v) => M.l + (v / 100) * (W - M.l - M.r)
const y = (v) => H - M.b - (v / 100) * (H - M.t - M.b)
const tipStyle = computed(() => tip.value ? { left: `${(tip.value.left / W) * 100}%`, top: `${(tip.value.top / H) * 100}%`, transform: 'translate(-50%, -120%)' } : {})
const relSeries = computed(() => (sum.value?.engines || [])
  .filter(e => e.reliability?.length)
  .map(e => ({ engine: e.engine, label: e.label, points: [...e.reliability].sort((a, b) => a.pred - b.pred) })))

const pct = (v) => (v == null ? '-' : `${v}%`)
const signed = (v) => (v == null ? '-' : `${v > 0 ? '+' : ''}${v}%`)
const won = (v) => (v == null ? '-' : `${Math.round(v).toLocaleString('ko-KR')}`)
const retCls = (p) => { const v = p.realized?.return_pct ?? p.mtm_pct; return v == null ? '' : v > 0 ? 't-up' : v < 0 ? 't-down' : '' }
const errMsg = (e) => e?.response?.data?.error || e?.message || '요청 실패'

async function loadList() {
  try {
    const params = { limit: 300 }
    if (ticker.value) params.ticker = ticker.value
    if (status.value) params.status = status.value
    list.value = (await getLedgerPredictions(params)).data
  } catch (e) { error.value = `예측 목록을 불러오지 못했습니다: ${errMsg(e)}` }
}
async function load() {
  error.value = ''
  if (ticker.value && !/^\d{6}$/.test(ticker.value)) { error.value = '종목코드는 숫자 6자리입니다'; return }
  try { sum.value = (await getLedgerSummary(ticker.value || undefined)).data } catch (e) { error.value = `성과를 불러오지 못했습니다: ${errMsg(e)}` }
  await loadList()
}
async function doScore() {
  busy.value = true; note.value = ''; error.value = ''
  try {
    const r = (await scoreLedger()).data
    note.value = `채점 완료 — ${r.updated}건 갱신 (상태 변경 ${r.status_changed}건${r.errors?.length ? ` · 가격 조회 실패 ${r.errors.length}건` : ''}) · ${r.sec}초`
    await load()
  } catch (e) { error.value = `채점 실패: ${errMsg(e)}` } finally { busy.value = false }
}
async function doBackfill() {
  busy.value = true; note.value = ''; error.value = ''
  try {
    const r = (await backfillLedger()).data
    note.value = `기존 판단 ${r.added}건을 가져왔습니다 (이미 있음 ${r.skipped}건)`
    if (r.added) await scoreLedger().catch(() => {})
    await load()
  } catch (e) { error.value = `가져오기 실패: ${errMsg(e)}` } finally { busy.value = false }
}
function openReport(p) { router.push({ path: '/stock', query: { report: p.id } }) }
onMounted(load)
</script>

<style scoped>
.dot { display: inline-block; width: 9px; height: 9px; border-radius: 3px; margin-right: 7px; vertical-align: 0; }
.rel-wrap { position: relative; }
.rel { width: 100%; height: auto; display: block; }
.rel .grid { stroke: #1e2533; stroke-width: 1; }
.rel .diag { stroke: #52607a; stroke-width: 1.5; stroke-dasharray: 4 4; }
.rel .tick { fill: #8892a4; font-size: 10px; font-family: 'JetBrains Mono', monospace; }
.rel .axis { fill: #8892a4; font-size: 10.5px; }
.rel circle { cursor: default; }
</style>
