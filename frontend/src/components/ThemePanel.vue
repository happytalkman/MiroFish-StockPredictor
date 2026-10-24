<template>
  <section class="tp" aria-labelledby="tp-title">
    <header class="tp-hd">
      <h2 id="tp-title" class="tp-title"><i class="tp-bar"></i>오늘의 테마</h2>
      <span v-if="data" class="tp-date mono">{{ dateLabel(data.date) }}</span>
      <span v-if="data && !isToday" class="tp-tag old" title="오늘 추출분이 아직 없습니다">지난 데이터</span>
      <span v-if="data" class="tp-tag" :class="data.engine === 'llm' ? 'ai' : 'rule'" :title="data.model ? '모델 ' + data.model : '테마 등락률·상승 종목 비율·뉴스 키워드 점수'">
        {{ data.engine === 'llm' ? 'AI 선정' : '규칙 기반' }}
      </span>
      <label v-if="dates.length > 1" class="tp-sel-wrap">
        <span class="sr-only">날짜 선택</span>
        <select class="tp-sel mono" :value="selDate" :disabled="loading" @change="onDate($event)">
          <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
        </select>
      </label>
      <div class="tp-hd-right">
        <span v-if="metaText" class="tp-meta" :title="schedTitle">{{ metaText }}</span>
        <button class="tp-btn primary" :disabled="busy" @click="refresh">
          <svg viewBox="0 0 24 24" width="13" height="13" :class="{ spin: busy }" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="23 4 23 10 17 10" />
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
          </svg>
          {{ busy ? '추출 중…' : '지금 새로 추출' }}
        </button>
      </div>
    </header>

    <ProgressModal
      :show="pm.show"
      title="오늘의 테마 추출 중"
      subtitle="네이버 테마 시세·구성 종목(1초 간격)과 오늘 뉴스 헤드라인을 모아 AI 가 Top 테마를 고릅니다."
      done-title="오늘의 테마를 새로 추출했습니다"
      :stages="pm.stages" :progress="pm.progress" :message="pm.message" :logs="pm.logs"
      :status="pm.status" :error="pm.error" :started-at="pm.startedAt" :expected-sec="45"
      @close="pmClose" @retry="pmClose(); refresh()"
    />

    <div v-if="bg.id && !pm.show" class="tp-bg" role="status">
      <span class="tp-dot"></span>자동 추출 진행 중<span v-if="bg.msg" class="muted"> · {{ bg.msg }}</span>
    </div>

    <div v-if="loading && !data" class="tp-body">
      <div class="tp-grid">
        <div v-for="n in 4" :key="n" class="tc sk-card">
          <div class="sk w60"></div>
          <div class="sk w35"></div>
          <div class="sk h6"></div>
          <div class="sk w90"></div>
          <div class="sk w80"></div>
        </div>
      </div>
    </div>
    <div v-else-if="error" class="tp-state err">
      <span>⚠ 테마를 불러오지 못했습니다 · {{ error }}</span>
      <button class="tp-btn ghost sm" @click="load(selDate)">다시 시도</button>
    </div>
    <div v-else-if="!data" class="tp-empty">
      <svg viewBox="0 0 48 48" width="42" height="42" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">
        <path d="M8 36l10-12 8 7 14-17" stroke="#a78bfa" stroke-linecap="round" stroke-linejoin="round" />
        <circle cx="40" cy="14" r="3" stroke="#a78bfa" />
        <line x1="6" y1="42" x2="42" y2="42" stroke="#2a3041" />
      </svg>
      <h3>아직 추출된 테마가 없습니다</h3>
      <p>
        평일 <b class="mono">{{ schedTime }}</b> 자동 추출 ·
        <button class="tp-link" :disabled="busy" @click="refresh">지금 추출하기</button>
      </p>
    </div>

    <div v-else class="tp-body">
      <div v-if="keywords.length" class="tp-kw" aria-label="오늘 뉴스 키워드">
        <span class="tp-label">뉴스 키워드</span>
        <span v-for="k in keywords" :key="k.word" class="kw" :class="{ hot: k.theme }" :title="k.theme ? k.theme + ' 테마와 연결' : '헤드라인 ' + k.count + '건'">
          {{ k.word }}<b class="mono">{{ k.count }}</b>
        </span>
      </div>

      <div v-if="!themes.length" class="tp-state">추출 결과에 테마가 없습니다. 다시 추출해 보세요.</div>
      <div v-else class="tp-grid">
        <article v-for="t in themes" :key="t.key" class="tc" :class="{ open: openKey === t.key }">
          <div
            class="tc-main"
            role="button"
            tabindex="0"
            :aria-expanded="openKey === t.key ? 'true' : 'false'"
            :aria-label="t.name + ' 테마 구성 종목 ' + (openKey === t.key ? '접기' : '펼치기')"
            @click="toggle(t)"
            @keydown.enter.prevent="toggle(t)"
            @keydown.space.prevent="toggle(t)"
          >
            <div class="tc-top">
              <span class="tc-rank mono">{{ pad(t.rank) }}</span>
              <h3 class="tc-name">{{ t.name }}</h3>
              <span class="tc-pct mono" :class="dir(t.change_pct)">{{ fmtPct(t.change_pct) }}</span>
            </div>

            <div class="tc-sub">
              <span class="tc-3d"><span class="muted">3일</span> <span class="mono" :class="dir(t.change_3d_pct)">{{ fmtPct(t.change_3d_pct) }}</span></span>
              <div class="tc-breadth" :title="`상승 ${t.b.up} · 보합 ${t.b.flat} · 하락 ${t.b.down}`">
                <span class="b-up" :style="{ width: t.b.upW + '%' }"></span>
                <span class="b-flat" :style="{ width: t.b.flatW + '%' }"></span>
                <span class="b-down" :style="{ width: t.b.downW + '%' }"></span>
              </div>
              <span class="tc-bn mono"><span class="up">▲{{ t.b.up }}</span> <span class="down">▼{{ t.b.down }}</span></span>
            </div>

            <div class="tc-mom" :title="'모멘텀 점수 ' + t.momentum_score + ' / 100'">
              <span class="tp-label">모멘텀</span>
              <div class="mom-bar"><span :class="momClass(t.momentum_score)" :style="{ width: clamp(t.momentum_score) + '%' }"></span></div>
              <b class="mono">{{ t.momentum_score ?? '—' }}</b>
            </div>

            <p class="tc-why" :class="{ clamp2: openKey !== t.key }">
              <template v-for="(seg, i) in t.whySegs" :key="i">
                <a
                  v-if="seg.ref && seg.url"
                  class="ref mono"
                  :href="seg.url"
                  target="_blank"
                  rel="noopener noreferrer"
                  :title="seg.title"
                  @click.stop
                >{{ seg.ref }}</a>
                <span v-else-if="seg.ref" class="ref mono dead">{{ seg.ref }}</span>
                <template v-else>{{ seg.text }}</template>
              </template>
            </p>

            <div v-if="t.leaders.length" class="tc-leaders">
              <span class="tp-label">주도주</span>
              <button v-for="l in t.leaders" :key="l.code" class="chip" :title="l.name + ' 5단계 분석'" @click.stop="analyze(l, t)">
                {{ l.name }}
              </button>
            </div>
          </div>

          <div class="tc-actions">
            <button class="tp-btn ghost sm" :disabled="!t.codes.length" :title="t.codes.length + '종목을 조건검색으로 검사'" @click.stop="screen(t)">
              스크리너로 검사
            </button>
            <button class="tp-btn ghost sm" :aria-expanded="openKey === t.key ? 'true' : 'false'" @click.stop="toggle(t)">
              {{ openKey === t.key ? '접기 ▴' : `구성 종목 ${t.members.length} ▾` }}
            </button>
          </div>

          <div v-if="openKey === t.key" class="tc-detail">
            <div v-if="t.risk_note" class="tc-risk">⚠ {{ t.risk_note }}</div>

            <ol v-if="t.news.length" class="tc-news">
              <li v-for="n in t.news" :key="n.id">
                <span class="nid mono">{{ n.id }}</span>
                <a :href="n.url" target="_blank" rel="noopener noreferrer" class="n-title">{{ n.title }}</a>
                <span v-if="n.press" class="n-press">{{ n.press }}</span>
              </li>
            </ol>

            <div v-if="t.keywords.length" class="tc-kws">
              <span v-for="w in t.keywords" :key="w" class="kw sm">#{{ w }}</span>
            </div>

            <div v-if="!t.members.length" class="tp-state sm">구성 종목 정보를 가져오지 못했습니다.</div>
            <div v-else class="tc-tbl-wrap">
              <table class="tc-tbl">
                <thead>
                  <tr>
                    <th class="l">종목</th>
                    <th class="r">현재가</th>
                    <th class="r">등락률</th>
                    <th class="r hide-sm">거래량</th>
                    <th class="c"><span class="sr-only">분석</span></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="m in t.members" :key="m.code">
                    <td class="l">
                      <div class="m-name">
                        <span>{{ m.name }}</span>
                        <span v-if="t.leaderSet.has(m.code)" class="lead">주도</span>
                      </div>
                      <div class="m-code mono">{{ m.code }}<template v-if="m.market"> · {{ mktShort(m.market) }}</template></div>
                      <div v-if="m.reason" class="m-reason" :title="m.reason">{{ m.reason }}</div>
                    </td>
                    <td class="r mono" :class="dir(m.change_pct)">{{ fmtNum(m.price) }}</td>
                    <td class="r"><span class="pill mono" :class="dir(m.change_pct)">{{ fmtPct(m.change_pct) }}</span></td>
                    <td class="r mono hide-sm">{{ fmtVol(m.volume) }}</td>
                    <td class="c"><button class="go" @click.stop="analyze(m, t)">5단계 분석</button></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </article>
      </div>

      <p v-if="notes" class="tp-foot">{{ notes }} · 정보 제공용이며 투자 권유가 아닙니다.</p>
    </div>
  </section>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api/index'
import ProgressModal from './ProgressModal.vue'
import { useProgress } from '../composables/useProgress.js'

const emit = defineEmits(['screen'])
const router = useRouter()

const data = ref(null)
const dates = ref([])
const selDate = ref('')
const sched = ref(null)
const loading = ref(true)
const error = ref('')
const busy = ref(false)
const openKey = ref('')
const bg = reactive({ id: '', msg: '' })
let alive = true
let pollTimer = null

const { pm, start: pmStart, log: pmLog, stage: pmStage, done: pmDone, fail: pmFail, close: pmClose, follow } = useProgress()
const STAGES = [
  { key: 'list', label: '네이버 테마 시세 수집' },
  { key: 'news', label: '뉴스 헤드라인 · 키워드' },
  { key: 'detail', label: '구성 종목 조회 (1초 간격)' },
  { key: 'rank', label: '테마 선정 (AI · 규칙)' },
  { key: 'save', label: '저장' }
]
const ORDER = STAGES.map(s => s.key)

/* ───────── 포맷 ───────── */
const isNum = v => typeof v === 'number' && Number.isFinite(v)
function toNum (v) {
  if (isNum(v)) return v
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v.replace(/,/g, ''))
    return Number.isFinite(n) ? n : null
  }
  return null
}
function fmtNum (v, d = 0) {
  const n = toNum(v)
  return n == null ? '—' : n.toLocaleString('ko-KR', { minimumFractionDigits: d, maximumFractionDigits: d })
}
function fmtPct (v, d = 2) {
  const n = toNum(v)
  if (n == null) return '—'
  if (Math.abs(n) < 0.5 * Math.pow(10, -d)) return (0).toFixed(d) + '%'
  return (n > 0 ? '+' : '') + n.toFixed(d) + '%'
}
function fmtVol (v) {
  const n = toNum(v)
  if (n == null) return '—'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '억'
  if (n >= 1e4) return fmtNum(n / 1e4) + '만'
  return fmtNum(n)
}
function dir (v) {
  const n = toNum(v)
  if (n == null || n === 0) return 'flat'
  return n > 0 ? 'up' : 'down'
}
function clamp (v) {
  const n = toNum(v)
  return n == null ? 0 : Math.max(0, Math.min(100, n))
}
function pad (n) { return String(n ?? '').padStart(2, '0') }
function mktShort (m) {
  const t = String(m || '').toUpperCase()
  return t === 'KOSPI' ? '코스피' : t === 'KOSDAQ' ? '코스닥' : t === 'KONEX' ? '코넥스' : (m || '')
}
function momClass (s) {
  const n = toNum(s) || 0
  return n >= 70 ? 'hot' : n >= 45 ? 'warm' : 'cool'
}
function kstToday () {
  return new Date(Date.now() + 9 * 3600e3).toISOString().slice(0, 10)
}
function dateLabel (s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s || ''))
  if (!m) return String(s || '')
  const wd = '일월화수목금토'[new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).getUTCDay()]
  return `${m[2]}.${m[3]} (${wd})`
}
function errMsg (e) {
  if (!e) return '알 수 없는 오류'
  const r = e.response && e.response.data
  if (r && typeof r === 'object' && r.error) return String(r.error)
  if (e.code === 'ECONNABORTED') return '응답 시간이 초과되었습니다'
  return e.message ? String(e.message) : '요청 실패'
}

/* ───────── 파생 ───────── */
const isToday = computed(() => !!data.value && data.value.date === kstToday())
const schedTime = computed(() => (sched.value && sched.value.time) || '08:40')
const metaText = computed(() => {
  const d = data.value
  const parts = []
  const g = d && typeof d.generated_at === 'string' ? d.generated_at.slice(11, 16) : ''
  if (g) parts.push(`${g} 추출`)
  if (sched.value && sched.value.enabled === false) parts.push('자동 추출 꺼짐')
  else parts.push(`평일 ${schedTime.value} 자동`)
  return parts.join(' · ')
})
const schedTitle = computed(() => {
  const s = sched.value
  if (!s) return ''
  const t = []
  if (s.next_run) t.push('다음 자동 추출 ' + s.next_run)
  if (s.last_error) t.push('최근 오류 ' + s.last_error)
  return t.join('\n')
})
const keywords = computed(() => {
  const k = data.value && Array.isArray(data.value.keywords) ? data.value.keywords : []
  return k.filter(x => x && x.word).slice(0, 14)
})
const notes = computed(() => {
  const n = data.value && Array.isArray(data.value.source_notes) ? data.value.source_notes : []
  return n.filter(Boolean).join(' · ')
})

function splitWhy (text, news) {
  const byId = Object.fromEntries((news || []).map(n => [n.id, n]))
  const out = []
  const re = /\[(N\d+)\]/g
  let last = 0
  let m
  const s = String(text || '')
  while ((m = re.exec(s))) {
    if (m.index > last) out.push({ text: s.slice(last, m.index) })
    const n = byId[m[1]]
    out.push({ ref: m[1], url: n ? n.url : '', title: n ? n.title : '' })
    last = m.index + m[0].length
  }
  if (last < s.length) out.push({ text: s.slice(last) })
  return out
}

const themes = computed(() => {
  const list = data.value && Array.isArray(data.value.themes) ? data.value.themes.filter(t => t && t.name) : []
  return list.map((t, i) => {
    const b = t.breadth || {}
    const up = toNum(b.up) || 0
    const flat = toNum(b.flat) || 0
    const down = toNum(b.down) || 0
    const tot = up + flat + down
    const news = Array.isArray(t.news) ? t.news.filter(n => n && n.id) : []
    const members = Array.isArray(t.members) ? t.members.filter(m => m && m.code) : []
    const leaders = Array.isArray(t.leaders) ? t.leaders.filter(l => l && l.code) : []
    const related = Array.isArray(t.related) ? t.related.filter(l => l && l.code) : []
    const codes = [...new Set((members.length ? members : [...leaders, ...related]).map(m => String(m.code)))]
    return {
      ...t,
      key: String(t.no || t.name) + ':' + i,
      rank: t.rank || i + 1,
      b: {
        up, flat, down,
        upW: tot ? (up / tot) * 100 : 0,
        flatW: tot ? (flat / tot) * 100 : 0,
        downW: tot ? (down / tot) * 100 : 0
      },
      news,
      members,
      leaders,
      leaderSet: new Set(leaders.map(l => String(l.code))),
      codes,
      keywords: Array.isArray(t.keywords) ? t.keywords.filter(Boolean) : [],
      whySegs: splitWhy(t.why, news)
    }
  })
})

/* ───────── 동작 ───────── */
function toggle (t) {
  openKey.value = openKey.value === t.key ? '' : t.key
}
function onDate (e) {
  const v = e && e.target ? e.target.value : ''
  selDate.value = v
  openKey.value = ''
  load(v)
}
function analyze (m, t) {
  if (!m || !m.code) return
  const mem = (t && t.members.find(x => String(x.code) === String(m.code))) || m
  const query = { ticker: String(m.code) }
  const name = mem.name || m.name
  if (name) query.name = String(name)
  if (mem.market) query.market = String(mem.market) // 시장을 모르면 넘기지 않음 (추측 금지)
  router.push({ path: '/stock', query })
}
function screen (t) {
  if (!t.codes.length) return
  emit('screen', { name: t.name, no: t.no || null, codes: t.codes.slice(0, 200) })
}

async function loadDates () {
  try {
    const r = await api.get('/api/market/themes/dates', { timeout: 15000 })
    const d = (r && r.data) || {}
    dates.value = Array.isArray(d.dates) ? d.dates : []
  } catch (e) { /* 날짜 목록은 선택 기능 */ }
}

async function load (date, { silent = false } = {}) {
  if (!silent) { loading.value = true; error.value = '' }
  try {
    const r = await api.get('/api/market/themes', { params: date ? { date } : {}, timeout: 15000 })
    const d = (r && r.data) || null
    sched.value = (d && d.scheduler) || sched.value
    data.value = d
    if (d && d.date) selDate.value = d.date
    error.value = ''
  } catch (e) {
    const res = e && e.response
    if (res && res.status === 404) {
      if (res.data && res.data.scheduler) sched.value = res.data.scheduler
      if (!date) data.value = null
      else if (!silent) error.value = errMsg(e)
    } else if (!silent) {
      error.value = errMsg(e)
    }
  } finally {
    if (!silent) loading.value = false
  }
  watchBackground()
}

/* 스케줄러가 돌리는 자동 추출을 조용히 따라가다 끝나면 새로 불러옴 */
function watchBackground () {
  const tid = sched.value && sched.value.running_task_id
  if (!tid || bg.id === tid || busy.value) return
  bg.id = tid
  bg.msg = ''
  follow(tid, { onUpdate: t => { bg.msg = t && t.message ? String(t.message).split(' — ')[0] : '' } })
    .catch(() => {})
    .finally(async () => {
      if (!alive) return
      bg.id = ''
      bg.msg = ''
      if (!busy.value) {
        await loadDates()
        await load(null, { silent: true })
      }
    })
}

async function refresh () {
  if (busy.value) return
  busy.value = true
  pmStart(STAGES.map(s => ({ ...s, status: 'pending' })))
  pmLog('오늘의 테마 추출을 요청합니다')
  try {
    const r = await api.post('/api/market/themes/refresh', {}, { timeout: 20000 })
    const tid = r && r.data && r.data.task_id
    if (!tid) throw new Error('작업 ID 를 받지 못했습니다')
    const t = await follow(tid, {
      onUpdate: (task) => {
        const st = (task.progress_detail || {}).stage
        if (!st) return
        const idx = st === 'done' ? ORDER.length : ORDER.indexOf(st)
        if (idx < 0) return
        ORDER.forEach((k, i) => pmStage(k, i < idx ? 'done' : i === idx ? 'running' : 'pending'))
      }
    })
    ORDER.forEach(k => pmStage(k, 'done'))
    const res = (t && t.result) || {}
    await loadDates()
    openKey.value = ''
    await load(null)
    pmDone(`테마 ${res.count ?? (data.value && data.value.themes ? data.value.themes.length : 0)}개 추출 완료 · ${res.engine === 'llm' ? 'AI 선정' : '규칙 기반'}`)
  } catch (e) {
    pmFail(errMsg(e))
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  await Promise.allSettled([load(null), loadDates()])
  // 오늘 데이터가 없으면 5분마다 조용히 확인 (08:40 자동 추출이 끝나면 반영)
  pollTimer = setInterval(() => {
    if (typeof document !== 'undefined' && document.hidden) return
    if (busy.value || bg.id || loading.value) return
    if (!data.value || !isToday.value) load(null, { silent: true })
  }, 300000)
})
onBeforeUnmount(() => {
  alive = false
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
})
</script>

<style scoped>
.tp {
  --tp-panel: var(--panel, #0f1419);
  --tp-panel-2: var(--panel-2, #121821);
  --tp-border: var(--border, #2a3041);
  --tp-subtle: var(--subtle, #1e2533);
  --tp-text: var(--text, #e2e8f0);
  --tp-muted: var(--muted, #8892a4);
  --tp-up: #ef4444;
  --tp-down: #3b82f6;
  --tp-violet: #a78bfa;
  --tp-amber: #f59e0b;
  --tp-green: #10b981;
  --tp-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  position: relative;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--tp-panel);
  border: 1px solid #332d4d;
  border-radius: 8px;
  overflow: hidden;
  color: var(--tp-text);
  font-family: 'Noto Sans KR', 'Inter', -apple-system, 'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
  font-size: 13px;
  line-height: 1.45;
}
.tp *, .tp *::before, .tp *::after { box-sizing: border-box; }
.tp button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
.tp button:disabled { cursor: not-allowed; opacity: 0.55; }
.tp button:focus-visible, .tp a:focus-visible, .tp select:focus-visible, .tp [role='button']:focus-visible {
  outline: 2px solid rgba(167, 139, 250, 0.7);
  outline-offset: 1px;
}
.tp h2, .tp h3, .tp p, .tp ol { margin: 0; padding: 0; }
.tp ol { list-style: none; }
.mono { font-family: var(--tp-mono); font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.muted { color: var(--tp-muted); }
.up { color: var(--tp-up); }
.down { color: var(--tp-down); }
.flat { color: var(--tp-muted); }
.sr-only { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* 헤더 */
.tp-hd {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 10px;
  min-height: 40px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--tp-subtle);
  background: linear-gradient(180deg, #16132a 0%, var(--tp-panel) 100%);
}
.tp-title { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 700; letter-spacing: 0.02em; white-space: nowrap; }
.tp-bar { display: inline-block; width: 3px; height: 13px; border-radius: 2px; background: var(--tp-violet); }
.tp-date { font-size: 12px; color: #c9d4e0; }
.tp-tag {
  font-size: 10.5px;
  font-weight: 600;
  padding: 1px 7px;
  border-radius: 999px;
  border: 1px solid var(--tp-border);
  color: var(--tp-muted);
  white-space: nowrap;
}
.tp-tag.ai { color: var(--tp-violet); border-color: rgba(167, 139, 250, 0.45); background: rgba(167, 139, 250, 0.1); }
.tp-tag.rule { color: #93c5fd; border-color: rgba(59, 130, 246, 0.4); background: rgba(59, 130, 246, 0.08); }
.tp-tag.old { color: var(--tp-amber); border-color: rgba(245, 158, 11, 0.45); background: rgba(245, 158, 11, 0.08); }
.tp-sel-wrap { display: inline-flex; }
.tp-sel {
  background: var(--tp-panel-2);
  color: var(--tp-text);
  border: 1px solid var(--tp-border);
  border-radius: 6px;
  padding: 2px 6px;
  font-size: 11.5px;
  max-width: 128px;
}
.tp-hd-right { margin-left: auto; display: flex; align-items: center; gap: 10px; min-width: 0; }
.tp-meta { font-size: 11px; color: var(--tp-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tp-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  transition: background 0.15s, border-color 0.15s;
}
.tp-btn.primary { background: rgba(167, 139, 250, 0.16); border: 1px solid rgba(167, 139, 250, 0.5); color: #ddd6fe; }
.tp-btn.primary:hover:not(:disabled) { background: rgba(167, 139, 250, 0.28); }
.tp-btn.ghost { border: 1px solid var(--tp-border); color: #c9d4e0; }
.tp-btn.ghost:hover:not(:disabled) { background: var(--tp-subtle); }
.tp-btn.sm { padding: 3px 10px; font-size: 11.5px; font-weight: 500; }
.tp-link { color: #c4b5fd; text-decoration: underline; text-underline-offset: 2px; }
.spin { animation: tp-spin 0.9s linear infinite; }
@keyframes tp-spin { to { transform: rotate(360deg); } }

.tp-bg {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 14px;
  font-size: 12px;
  color: #ddd6fe;
  background: rgba(167, 139, 250, 0.08);
  border-bottom: 1px solid var(--tp-subtle);
}
.tp-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--tp-violet); animation: tp-pulse 1.2s ease-in-out infinite; flex: none; }
@keyframes tp-pulse { 50% { opacity: 0.3; } }

/* 상태 */
.tp-state { padding: 22px 16px; text-align: center; color: var(--tp-muted); display: flex; gap: 10px; justify-content: center; align-items: center; flex-wrap: wrap; }
.tp-state.err { color: #fca5a5; }
.tp-state.sm { padding: 12px; font-size: 12px; }
.tp-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 26px 16px 28px; text-align: center; color: var(--tp-muted); }
.tp-empty h3 { font-size: 14px; color: var(--tp-text); font-weight: 600; }
.tp-empty p { font-size: 12.5px; }

/* 본문 */
.tp-body { padding: 12px 14px 14px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.tp-label { font-size: 10.5px; font-weight: 600; letter-spacing: 0.1em; color: var(--tp-muted); white-space: nowrap; }
.tp-kw { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.kw {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--tp-panel-2);
  border: 1px solid var(--tp-subtle);
  font-size: 11.5px;
  color: #c9d4e0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kw b { font-size: 10px; color: var(--tp-muted); font-weight: 500; }
.kw.hot { border-color: rgba(167, 139, 250, 0.4); color: #ddd6fe; }
.kw.sm { font-size: 11px; padding: 1px 7px; }

.tp-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; min-width: 0; }
.tc {
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--tp-panel-2);
  border: 1px solid var(--tp-subtle);
  border-radius: 8px;
  transition: border-color 0.15s;
}
.tc:hover { border-color: #3a3f5c; }
.tc.open { grid-column: 1 / -1; border-color: rgba(167, 139, 250, 0.45); }
.tc-main { padding: 10px 12px 6px; display: flex; flex-direction: column; gap: 7px; cursor: pointer; min-width: 0; border-radius: 8px 8px 0 0; }
.tc-top { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.tc-rank { font-size: 12px; color: var(--tp-violet); font-weight: 700; flex: none; }
.tc-name { flex: 1 1 auto; min-width: 0; font-size: 14px; font-weight: 700; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tc.open .tc-name { white-space: normal; }
.tc-pct { flex: none; font-size: 14px; font-weight: 700; }
.tc-sub { display: flex; align-items: center; gap: 8px; font-size: 11.5px; min-width: 0; }
.tc-3d { flex: none; white-space: nowrap; }
.tc-breadth { flex: 1 1 60px; min-width: 40px; height: 6px; display: flex; border-radius: 3px; overflow: hidden; background: var(--tp-subtle); }
.tc-breadth span { height: 100%; }
.b-up { background: var(--tp-up); }
.b-flat { background: #4b5566; }
.b-down { background: var(--tp-down); }
.tc-bn { flex: none; font-size: 11px; white-space: nowrap; }
.tc-mom { display: flex; align-items: center; gap: 8px; }
.mom-bar { flex: 1; height: 5px; border-radius: 3px; background: var(--tp-subtle); overflow: hidden; }
.mom-bar span { display: block; height: 100%; border-radius: 3px; }
.mom-bar .hot { background: linear-gradient(90deg, #f97316, var(--tp-up)); }
.mom-bar .warm { background: linear-gradient(90deg, #a78bfa, #f59e0b); }
.mom-bar .cool { background: #64748b; }
.tc-mom b { font-size: 12px; min-width: 22px; text-align: right; }
.tc-why { font-size: 12.5px; color: #c9d4e0; line-height: 1.55; word-break: keep-all; overflow-wrap: anywhere; }
.tc-why.clamp2 { display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.ref {
  display: inline-block;
  margin: 0 1px;
  padding: 0 4px;
  border-radius: 4px;
  font-size: 10.5px;
  line-height: 1.5;
  color: #c4b5fd;
  background: rgba(167, 139, 250, 0.12);
  text-decoration: none;
  vertical-align: 1px;
}
.ref:hover { background: rgba(167, 139, 250, 0.28); }
.ref.dead { color: var(--tp-muted); background: var(--tp-subtle); }
.tc-leaders { display: flex; flex-wrap: wrap; align-items: center; gap: 5px; }
.chip {
  font-size: 11.5px;
  padding: 1px 8px;
  border-radius: 999px;
  border: 1px solid var(--tp-border);
  color: var(--tp-text);
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chip:hover { border-color: var(--tp-violet); color: #ddd6fe; }
.tc-actions { display: flex; gap: 6px; justify-content: space-between; padding: 6px 12px 10px; flex-wrap: wrap; }

/* 펼친 상세 */
.tc-detail { border-top: 1px solid var(--tp-subtle); padding: 10px 12px 12px; display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.tc-risk { font-size: 12px; color: #fcd34d; background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 6px; padding: 6px 10px; }
.tc-news { display: flex; flex-direction: column; gap: 5px; }
.tc-news li { display: flex; align-items: baseline; gap: 6px; font-size: 12px; min-width: 0; }
.nid { flex: none; font-size: 10.5px; color: #c4b5fd; }
.n-title { flex: 1 1 auto; min-width: 0; color: var(--tp-text); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.n-title:hover { color: #c4b5fd; text-decoration: underline; }
.n-press { flex: none; font-size: 11px; color: var(--tp-muted); }
.tc-kws { display: flex; flex-wrap: wrap; gap: 5px; }
.tc-tbl-wrap { overflow-x: auto; max-width: 100%; max-height: 460px; overflow-y: auto; border: 1px solid var(--tp-subtle); border-radius: 6px; }
.tc-tbl { width: 100%; border-collapse: collapse; font-size: 12px; }
.tc-tbl th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #151b26;
  color: var(--tp-muted);
  font-weight: 600;
  font-size: 11px;
  padding: 6px 8px;
  border-bottom: 1px solid var(--tp-subtle);
  white-space: nowrap;
}
.tc-tbl td { padding: 6px 8px; border-bottom: 1px solid var(--tp-subtle); vertical-align: middle; }
.tc-tbl tr:last-child td { border-bottom: 0; }
.tc-tbl tbody tr:hover td { background: rgba(167, 139, 250, 0.05); }
.l { text-align: left; }
.r { text-align: right; white-space: nowrap; }
.c { text-align: center; }
.m-name { display: flex; align-items: center; gap: 6px; font-weight: 600; }
.lead { font-size: 10px; font-weight: 600; color: var(--tp-violet); border: 1px solid rgba(167, 139, 250, 0.4); border-radius: 4px; padding: 0 4px; }
.m-code { font-size: 10.5px; color: var(--tp-muted); }
.m-reason { font-size: 11px; color: var(--tp-muted); max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pill { display: inline-block; min-width: 62px; padding: 1px 6px; border-radius: 4px; font-size: 11.5px; text-align: right; background: var(--tp-subtle); }
.pill.up { background: rgba(239, 68, 68, 0.13); }
.pill.down { background: rgba(59, 130, 246, 0.15); }
.go {
  padding: 3px 9px;
  border-radius: 5px;
  border: 1px solid rgba(59, 130, 246, 0.45);
  color: #93c5fd;
  font-size: 11px;
  white-space: nowrap;
}
.go:hover { background: #3b82f6; color: #fff; border-color: #3b82f6; }
.tp-foot { font-size: 10.5px; color: var(--tp-muted); line-height: 1.5; }

/* 스켈레톤 */
.sk-card { padding: 12px; gap: 9px; }
.sk { height: 12px; border-radius: 4px; background: linear-gradient(90deg, #161c27 0%, #1f2736 50%, #161c27 100%); background-size: 200% 100%; animation: tp-sk 1.3s ease-in-out infinite; }
.sk.h6 { height: 6px; }
.sk.w35 { width: 35%; }
.sk.w60 { width: 60%; }
.sk.w80 { width: 80%; }
.sk.w90 { width: 90%; }
@keyframes tp-sk { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

@media (max-width: 640px) {
  .tp-hd { padding: 8px 12px; }
  .tp-hd-right { flex: 1 1 100%; justify-content: space-between; margin-left: 0; }
  .tp-body { padding: 10px 12px 12px; }
  .tp-grid { grid-template-columns: minmax(0, 1fr); }
  .hide-sm { display: none; }
  .m-reason { max-width: 180px; }
  .tc-tbl td, .tc-tbl th { padding: 6px 6px; }
}
@media (prefers-reduced-motion: reduce) {
  .spin, .sk, .tp-dot { animation: none !important; }
}
</style>
