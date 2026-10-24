<template>
  <AppShell sub="보고서 보관함 · 실행 비교">
    <div class="t-intro">
      <div>
        <h1>보고서 보관함</h1>
        <p>지금까지 만든 보고서·지식그래프·판단을 다시 열거나, 여러 번의 실행을 나란히 비교합니다. 같은 종목을 여러 번 돌린 결과를 고르면 목표가·확률이 실행마다 얼마나 흔들리는지 함께 보여 줍니다.</p>
      </div>
      <div class="t-actions">
        <input v-model.trim="q" class="t-input" placeholder="종목명·코드 검색" aria-label="종목 검색" @keydown.enter="load" />
        <button class="t-btn sm" @click="load">검색</button>
        <button class="t-btn sm primary" :disabled="selected.length < 2" @click="doCompare">선택 {{ selected.length }}개 비교</button>
      </div>
    </div>
    <div v-if="error" class="t-banner err">{{ error }}</div>

    <div v-if="cmp" class="t-card cmp">
      <div class="t-head">
        <h2>실행 비교 <span class="t-sub">{{ cmp.items.length }}개{{ cmp.same_ticker ? ' · 같은 종목' : '' }}</span></h2>
        <button class="t-btn sm ghost" @click="cmp = null">닫기</button>
      </div>
      <div v-if="cmp.same_ticker && cmp.spread" class="spread">
        <div class="t-kpi"><span>목표주가 범위</span><b>{{ rangeTxt(cmp.spread.target_price, won) }}</b><small>차이 {{ cmp.spread.target_price ? won(cmp.spread.target_price.range) : '-' }}</small></div>
        <div class="t-kpi"><span>상승 확률 범위 (판단)</span><b>{{ rangeTxt(cmp.spread.up_prob, pctTxt) }}</b><small>차이 {{ cmp.spread.up_prob ? cmp.spread.up_prob.range + '%p' : '-' }}</small></div>
        <div class="t-kpi"><span>계획 목표가 범위</span><b>{{ rangeTxt(cmp.spread.plan_target, won) }}</b><small>손절 {{ rangeTxt(cmp.spread.plan_stop, won) }}</small></div>
        <div class="t-kpi"><span>의견·전략</span><b class="small-b">{{ (cmp.spread.ratings || []).join('·') || '-' }}</b><small>{{ (cmp.spread.verdicts || []).join(' · ') || '판단 없음' }}</small></div>
      </div>
      <div class="t-scroll">
        <table class="t-table cmp-table">
          <thead><tr><th>항목</th><th v-for="it in cmp.items" :key="it.report_id">{{ it.company_name }}<br /><span class="t-muted t-small t-mono">{{ (it.created_at || '').replace('T', ' ').slice(0, 16) }}</span></th></tr></thead>
          <tbody>
            <tr v-for="row in CMP_ROWS" :key="row.label">
              <td class="t-muted">{{ row.label }}</td>
              <td v-for="it in cmp.items" :key="it.report_id + row.label" class="t-mono">{{ row.get(it) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div class="t-card">
      <div class="t-head">
        <h2>보고서 <span class="t-sub">{{ items.length }}건</span></h2>
        <label class="t-small t-muted"><input type="checkbox" v-model="onlyDecided" /> 판단까지 끝난 것만</label>
      </div>
      <div class="t-scroll">
        <table class="t-table">
          <thead>
            <tr><th></th><th>작성</th><th>종목</th><th>의견</th><th class="num">현재가</th><th class="num">목표가</th><th class="num">상승여력</th>
              <th>판단</th><th class="num">상승 확률</th><th>계획 (목표 / 손절)</th><th>단계</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="it in shown" :key="it.report_id">
              <td><input type="checkbox" :value="it.report_id" v-model="selected" :aria-label="it.company_name + ' 선택'" /></td>
              <td class="t-mono t-small nowrap" :title="(it.created_at || '').replace('T', ' ').slice(0, 19)">{{ (it.created_at || '').slice(5, 16).replace('T', ' ') }}</td>
              <td class="nowrap">{{ it.company_name }} <span class="t-muted t-mono">{{ it.ticker }}</span></td>
              <td><span class="t-pill" :class="it.rating === '매수' ? 'ok' : it.rating === '매도' ? 'err' : 'warn'"><i></i>{{ it.rating || '-' }}</span></td>
              <td class="num">{{ won(it.last_close) }}</td>
              <td class="num">{{ won(it.target_price) }}</td>
              <td class="num" :class="(it.upside_pct || 0) > 0 ? 't-up' : 't-down'">{{ pctTxt(it.upside_pct, true) }}</td>
              <td class="nowrap">{{ it.verdict ? `${it.verdict} ${it.verdict_pct}%` : '-' }}</td>
              <td class="num">{{ it.direction_probs ? it.direction_probs['상승'] + '%' : '-' }}<span v-if="it.calibrated" class="t-muted t-small"> (보정 {{ it.calibrated['상승'] }}%)</span></td>
              <td class="t-mono t-small">{{ it.plan ? `${won(it.plan.target_price)} / ${won(it.plan.stop_price)}` : '-' }}</td>
              <td class="t-small nowrap">
                <span class="stage" :class="{ on: true }">보고서</span>
                <span class="stage" :class="{ on: it.has_kg }">KG</span>
                <span class="stage" :class="{ on: it.has_decision }">판단</span>
              </td>
              <td class="acts">
                <button class="t-btn sm" @click="open(it, 2)">열기</button>
                <button v-if="it.has_decision" class="t-btn sm ghost" @click="open(it, 4)">판단</button>
              </td>
            </tr>
            <tr v-if="!shown.length"><td colspan="12" class="t-empty">보고서가 없습니다</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </AppShell>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppShell from '../components/AppShell.vue'
import '../assets/terminal-ui.css'
import { listReports, compareReports } from '../api/library.js'

const router = useRouter()
const route = useRoute()
const items = ref([])
const q = ref(String(route.query.q || ''))
const selected = ref([])
const cmp = ref(null)
const error = ref('')
const onlyDecided = ref(false)
const shown = computed(() => onlyDecided.value ? items.value.filter(i => i.has_decision) : items.value)

const won = (v) => (v == null ? '-' : `${Math.round(v).toLocaleString('ko-KR')}`)
const pctTxt = (v, signed = false) => (v == null ? '-' : `${signed && v > 0 ? '+' : ''}${v}%`)
const rangeTxt = (r, f) => (r ? `${f(r.min)} ~ ${f(r.max)}` : '-')
const CMP_ROWS = [
  { label: '투자의견', get: (i) => i.rating || '-' },
  { label: '목표주가', get: (i) => won(i.target_price) },
  { label: '상승여력', get: (i) => pctTxt(i.upside_pct, true) },
  { label: '밸류에이션', get: (i) => i.valuation_method || '-' },
  { label: '컨센서스 괴리', get: (i) => pctTxt(i.consensus_gap_pct, true) },
  { label: '판단 전략', get: (i) => (i.verdict ? `${i.verdict} ${i.verdict_pct}%` : '-') },
  { label: '방향 확률 (상승·보합·하락)', get: (i) => (i.direction_probs ? ['상승', '보합', '하락'].map(k => i.direction_probs[k]).join(' · ') : '-') },
  { label: '보정 확률', get: (i) => (i.calibrated ? ['상승', '보합', '하락'].map(k => i.calibrated[k]).join(' · ') : '-') },
  { label: '몬테카를로 상승', get: (i) => (i.mc?.direction_probs ? i.mc.direction_probs['상승'] + '%' : '-') },
  { label: '계획 진입 / 목표 / 손절', get: (i) => (i.plan ? `${won(i.plan.entry_price)} / ${won(i.plan.target_price)} / ${won(i.plan.stop_price)}` : '-') },
  { label: '비중 · 기간', get: (i) => (i.plan ? `${i.plan.position_pct ?? '-'}% · ${i.plan.horizon || '-'}` : '-') },
  { label: '위원회 (강세:약세)', get: (i) => (i.committee ? `${i.committee.bull_score}:${i.committee.bear_score} ${i.committee.consensus_label || ''}` : '-') },
  { label: '지식그래프', get: (i) => (i.kg ? `노드 ${i.kg.nodes} · CQ ${i.kg.cqs} · ADR ${i.kg.adrs}` : '-') },
  { label: '원장 결과', get: (i) => (i.ledger ? (i.ledger.realized ? `${i.ledger.realized.class} ${pctTxt(i.ledger.realized.return_pct, true)} ${i.ledger.strategy_hit ? '✓' : '✕'}` : `진행 중 ${pctTxt(i.ledger.mtm_pct, true)}`) : '-') },
  { label: '보고서 생성', get: (i) => (i.generation_sec ? `${Math.round(i.generation_sec)}초${i.speed ? ' · ' + i.speed : ''}` : '-') },
]

async function load() {
  error.value = ''
  try {
    const params = { limit: 400 }
    if (/^\d{6}$/.test(q.value)) params.ticker = q.value
    else if (q.value) params.q = q.value
    items.value = (await listReports(params)).data
    selected.value = selected.value.filter(id => items.value.some(i => i.report_id === id))
  } catch (e) { error.value = `목록을 불러오지 못했습니다: ${e?.response?.data?.error || e.message}` }
}
async function doCompare() {
  try { cmp.value = (await compareReports(selected.value.slice(0, 6))).data; window.scrollTo({ top: 0, behavior: 'smooth' }) }
  catch (e) { error.value = `비교 실패: ${e?.response?.data?.error || e.message}` }
}
function open(it, step) { router.push({ path: '/stock', query: { report: it.report_id, step } }) }
onMounted(load)
</script>

<style scoped>
.spread { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 12px; }
.small-b { font-size: 15px !important; }
.cmp-table td, .cmp-table th { min-width: 150px; }
.cmp-table td:first-child { min-width: 170px; }
.nowrap { white-space: nowrap; }
.stage { white-space: nowrap; display: inline-block; padding: 1px 6px; margin-right: 3px; border-radius: 4px; background: var(--subtle); color: var(--muted); font-size: 11px; }
.stage.on { background: rgba(16, 185, 129, .14); color: #6ee7b7; }
.acts { white-space: nowrap; }
.acts .t-btn + .t-btn { margin-left: 4px; }
</style>
