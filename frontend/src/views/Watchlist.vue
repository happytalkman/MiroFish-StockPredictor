<template>
  <AppShell sub="관심종목 · 자동 분석 · 알림">
    <div class="t-intro">
      <div>
        <h1>관심종목</h1>
        <p>관심종목을 한꺼번에 1→5단계로 분석하고, 매매계획의 진입가·목표가·손절가에 닿으면 알려 줍니다.
          아침 자동 분석은 <b>{{ sched?.enabled ? `켜짐 · 평일 ${sched.time}` : '꺼짐' }}</b> 상태입니다 (설정 → 스케줄에서 변경).</p>
      </div>
      <div class="t-actions">
        <button class="t-btn sm primary" :disabled="!items.length || !!running" @click="runAll()">{{ running ? '일괄 분석 중…' : `전체 ${items.length}개 분석` }}</button>
        <button class="t-btn sm ghost" @click="router.push('/settings')">알림·스케줄 설정</button>
      </div>
    </div>
    <div v-if="error" class="t-banner err">{{ error }}</div>
    <div v-if="note" class="t-banner ok">{{ note }}</div>

    <div class="t-grid2 top">
      <div class="t-card">
        <div class="t-head"><h2>종목 추가</h2><span class="t-sub">{{ items.length }}/40</span></div>
        <div class="add">
          <input v-model.trim="query" class="t-input" placeholder="종목명 또는 코드" aria-label="종목 검색" @input="onSearch" @keydown.enter="onSearch" />
        </div>
        <ul v-if="results.length" class="results">
          <li v-for="r in results" :key="r.ticker">
            <span>{{ r.name }} <span class="t-muted t-mono">{{ r.ticker }}</span> <span class="t-muted t-small">{{ r.market }}</span></span>
            <button class="t-btn sm" :disabled="items.some(i => i.ticker === r.ticker)" @click="add(r)">{{ items.some(i => i.ticker === r.ticker) ? '추가됨' : '추가' }}</button>
          </li>
        </ul>
        <p v-else-if="query && searched" class="t-muted t-small">검색 결과가 없습니다</p>
      </div>

      <div class="t-card">
        <div class="t-head">
          <h2>알림 <span v-if="alerts.unread" class="badge">{{ alerts.unread }}</span></h2>
          <div class="t-actions">
            <button class="t-btn sm ghost" :disabled="checking" @click="checkNow">{{ checking ? '확인 중…' : '지금 가격 확인' }}</button>
            <button class="t-btn sm ghost" :disabled="!alerts.unread" @click="markAll">모두 읽음</button>
          </div>
        </div>
        <p class="t-small t-muted">
          장중 {{ alerts.status?.interval_min || 5 }}분마다 확인 · {{ alerts.status?.enabled ? '켜짐' : '꺼짐' }} ·
          텔레그램 {{ alerts.status?.telegram ? '연결됨' : '미설정 (설정 → 알림에서 봇 토큰·채팅 ID)' }}
          <template v-if="alerts.status?.last_check"> · 마지막 확인 {{ alerts.status.last_check.slice(11, 16) }}</template>
        </p>
        <ul class="alerts">
          <li v-for="a in alerts.items" :key="a.id" :class="{ unread: !a.read }">
            <span class="kind" :class="a.kind">{{ a.kind_label }}</span>
            <div class="ab"><b>{{ a.title }}</b><span class="t-small t-muted">{{ a.body }}</span></div>
            <span class="t-small t-muted t-mono">{{ (a.created_at || '').slice(5, 16).replace('T', ' ') }}</span>
          </li>
          <li v-if="!alerts.items?.length" class="t-empty">아직 알림이 없습니다</li>
        </ul>
      </div>
    </div>

    <div v-if="running" class="t-card" style="margin-top:14px">
      <div class="t-head"><h2>일괄 분석 진행 <span class="t-sub">{{ running.trigger === 'schedule' ? '자동' : '수동' }} · 시작 {{ running.started_at?.slice(11, 16) }}</span></h2></div>
      <div class="runlist">
        <div v-for="it in running.items" :key="it.ticker" class="runrow" :class="it.status">
          <b>{{ it.name }}</b><span class="t-pill" :class="it.status === 'done' ? 'ok' : it.status === 'failed' ? 'err' : it.status === 'running' ? 'warn' : ''"><i></i>{{ it.stage }}</span>
          <span class="t-small t-muted msg">{{ it.message }}</span>
        </div>
      </div>
    </div>

    <div class="t-card" style="margin-top:14px">
      <div class="t-head"><h2>목록</h2></div>
      <div class="t-scroll">
        <table class="t-table">
          <thead><tr><th>종목</th><th>최근 분석</th><th>의견</th><th class="num">목표가</th><th>판단</th><th class="num">상승 확률</th><th>계획 (진입 / 목표 / 손절)</th><th></th></tr></thead>
          <tbody>
            <tr v-for="it in items" :key="it.ticker">
              <td>{{ it.name }} <span class="t-muted t-mono">{{ it.ticker }}</span></td>
              <td class="t-mono t-small">{{ it.latest ? (it.latest.created_at || '').replace('T', ' ').slice(0, 16) : '없음' }}</td>
              <td>{{ it.latest?.rating || '-' }}</td>
              <td class="num">{{ won(it.latest?.target_price) }}</td>
              <td>{{ it.latest?.verdict ? `${it.latest.verdict} ${it.latest.verdict_pct}%` : '-' }}</td>
              <td class="num">{{ it.latest?.direction_probs ? it.latest.direction_probs['상승'] + '%' : '-' }}</td>
              <td class="t-mono t-small">{{ it.latest?.plan ? `${won(it.latest.plan.entry_price)} / ${won(it.latest.plan.target_price)} / ${won(it.latest.plan.stop_price)}` : '-' }}</td>
              <td class="acts">
                <button class="t-btn sm" :disabled="!!running" @click="runAll([it.ticker])">분석</button>
                <button v-if="it.latest" class="t-btn sm ghost" @click="router.push({ path: '/stock', query: { report: it.latest.report_id, step: it.latest.has_decision ? 4 : 2 } })">열기</button>
                <button class="t-btn sm ghost danger" @click="remove(it)">삭제</button>
              </td>
            </tr>
            <tr v-if="!items.length"><td colspan="8" class="t-empty">관심종목을 추가하세요</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="history.length" class="t-card" style="margin-top:14px">
      <div class="t-head"><h2>최근 일괄 실행</h2></div>
      <table class="t-table">
        <thead><tr><th>시작</th><th>방식</th><th>결과</th><th>종목별</th></tr></thead>
        <tbody>
          <tr v-for="r in history" :key="r.id">
            <td class="t-mono t-small">{{ (r.started_at || '').replace('T', ' ').slice(0, 16) }}</td>
            <td>{{ r.trigger === 'schedule' ? '자동' : '수동' }}</td>
            <td>{{ r.items.filter(i => i.status === 'done').length }}/{{ r.items.length }} 성공</td>
            <td class="t-small">{{ r.items.map(i => `${i.name} ${i.verdict || (i.status === 'failed' ? '실패' : i.stage)}`).join(' · ') }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </AppShell>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import AppShell from '../components/AppShell.vue'
import '../assets/terminal-ui.css'
import { searchStock } from '../api/stock.js'
import { getWatchlist, addWatch, removeWatch, runWatchlist, getWatchRuns, getAlerts, readAlerts, checkAlerts } from '../api/library.js'

const router = useRouter()
const items = ref([])
const sched = ref(null)
const running = ref(null)
const history = ref([])
const alerts = ref({ items: [], unread: 0, status: null })
const query = ref('')
const results = ref([])
const searched = ref(false)
const error = ref('')
const note = ref('')
const checking = ref(false)
let timer = null
let searchTimer = null

const won = (v) => (v == null ? '-' : `${Math.round(v).toLocaleString('ko-KR')}`)
const errMsg = (e) => e?.response?.data?.error || e?.message || '요청 실패'

async function load() {
  try {
    const d = (await getWatchlist()).data
    items.value = d.items
    sched.value = d.schedule
    running.value = d.schedule?.running || null
    alerts.value.status = d.alerts
  } catch (e) { error.value = `관심종목을 불러오지 못했습니다: ${errMsg(e)}` }
  try { alerts.value = { ...(await getAlerts()).data } } catch (e) { /* 알림 없음 */ }
  try { history.value = (await getWatchRuns()).data.filter(r => r.status !== 'running').slice(0, 6) } catch (e) { /* 없음 */ }
  clearTimeout(timer)
  timer = setTimeout(load, running.value ? 5000 : 60000)
}
function onSearch() {
  clearTimeout(searchTimer)
  if (!query.value) { results.value = []; return }
  searchTimer = setTimeout(async () => {
    try {
      const r = await searchStock(query.value)
      results.value = (r.data || r.results || []).slice(0, 8).map(x => ({ ticker: x.ticker || x.code, name: x.name, market: x.market || '' }))
    } catch (e) { results.value = [] }
    searched.value = true
  }, 250)
}
async function add(r) {
  try { await addWatch(r.ticker, r.name, r.market); note.value = `${r.name} 추가`; await load() } catch (e) { error.value = errMsg(e) }
}
async function remove(it) {
  try { await removeWatch(it.ticker); await load() } catch (e) { error.value = errMsg(e) }
}
async function runAll(tickers) {
  error.value = ''; note.value = ''
  try {
    const r = (await runWatchlist(tickers)).data
    running.value = r.run
    note.value = `${r.run.items.length}개 종목 분석을 시작했습니다 (종목당 3~6분)`
    clearTimeout(timer); timer = setTimeout(load, 3000)
  } catch (e) { error.value = errMsg(e) }
}
async function checkNow() {
  checking.value = true
  try { const r = (await checkAlerts()).data; note.value = `진행 중 계획 ${r.checked}개 확인 · 새 알림 ${r.fired}개`; await load() }
  catch (e) { error.value = errMsg(e) } finally { checking.value = false }
}
async function markAll() { try { await readAlerts(); await load() } catch (e) { error.value = errMsg(e) } }
onMounted(load)
onBeforeUnmount(() => { clearTimeout(timer); clearTimeout(searchTimer) })
</script>

<style scoped>
.top { margin-top: 0; }
.add { display: flex; gap: 8px; }
.add .t-input { flex: 1; }
.results { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.results li { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 6px; background: var(--panel-2); }
.badge { display: inline-block; min-width: 18px; padding: 0 6px; border-radius: 99px; background: var(--red); color: #fff; font-size: 11px; text-align: center; margin-left: 6px; }
.alerts { list-style: none; margin: 8px 0 0; padding: 0; max-height: 280px; overflow-y: auto; }
.alerts li { display: grid; grid-template-columns: 80px 1fr auto; gap: 10px; align-items: start; padding: 8px 4px; border-bottom: 1px solid var(--subtle); }
.alerts li.unread { background: rgba(59, 130, 246, .06); }
.alerts .ab { display: flex; flex-direction: column; min-width: 0; }
.alerts .ab b { font-size: 12.5px; font-weight: 600; }
.kind { font-size: 11px; padding: 2px 6px; border-radius: 4px; background: var(--subtle); color: var(--text-2); text-align: center; }
.kind.target { background: rgba(239, 68, 68, .16); color: #fca5a5; }
.kind.stop { background: rgba(59, 130, 246, .16); color: #93c5fd; }
.kind.entry { background: rgba(245, 158, 11, .16); color: #fbbf24; }
.runlist { display: flex; flex-direction: column; gap: 6px; }
.runrow { display: grid; grid-template-columns: 140px 110px 1fr; gap: 10px; align-items: center; }
.runrow .msg { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.acts { white-space: nowrap; }
.acts .t-btn + .t-btn { margin-left: 4px; }
@media (max-width: 720px) { .runrow { grid-template-columns: 1fr auto; } .runrow .msg { grid-column: 1 / -1; } }
</style>
