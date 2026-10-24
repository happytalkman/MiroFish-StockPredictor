<template>
  <AppShell sub="누적 온톨로지 · SHACL · SPARQL">
    <div class="t-intro">
      <div>
        <h1>누적 온톨로지</h1>
        <p>보고서마다 만든 지식그래프를 하나의 그래프로 쌓습니다. 종목·공유 개념·테마는 보고서를 넘어 이어지고, 지표·신호·사건은 그 보고서 안에만 둡니다.
          새 분석을 돌리면 같은 종목의 과거 판단과 결과, 이전 ADR, 같은 개념으로 이어진 다른 종목이 사실표 H# 로 들어갑니다.</p>
      </div>
      <div class="t-actions">
        <button class="t-btn sm" :disabled="busy" @click="doBackfill">{{ busy === 'backfill' ? '적재 중…' : '전체 다시 적재' }}</button>
        <a class="t-btn sm" :href="ttlUrl()" download>Turtle 내려받기</a>
        <a class="t-btn sm ghost" :href="ttlUrl(true)" download>SHACL shapes</a>
      </div>
    </div>
    <div v-if="error" class="t-banner err">{{ error }}</div>
    <div v-if="note" class="t-banner ok">{{ note }}</div>

    <div class="t-kpis" v-if="st">
      <div class="t-kpi"><span>트리플</span><b>{{ fmt(st.triples) }}</b><small>개체 {{ fmt(st.entities) }}</small></div>
      <div class="t-kpi"><span>적재된 보고서</span><b>{{ fmt(st.reports) }}</b><small>보고서·판단·ADR·CQ</small></div>
      <div class="t-kpi"><span>종목</span><b>{{ fmt(st.stocks) }}</b><small>보고서·테마·개념 연결 포함</small></div>
      <div class="t-kpi"><span>공유 개념·키워드</span><b>{{ sharedMulti }}</b><small>두 종목 이상에 등장</small></div>
      <div class="t-kpi"><span>질의·검증 도구</span>
        <b class="small-b">
          <span class="t-pill" :class="st.rdflib ? 'ok' : 'warn'"><i></i>rdflib</span>
          <span class="t-pill" :class="st.pyshacl ? 'ok' : 'warn'"><i></i>pyshacl</span>
        </b>
        <small>{{ st.rdflib && st.pyshacl ? 'SPARQL·표준 SHACL 사용 가능' : '없으면 내장 규칙으로 검증' }}</small>
      </div>
    </div>

    <div v-if="st && !st.triples" class="t-banner info" style="margin-top:14px">
      아직 적재된 그래프가 없습니다. <b>전체 다시 적재</b>를 누르면 지금까지 만든 보고서·지식그래프·판단·테마를 한 번에 가져옵니다. 이후에는 4단계(지식그래프)·5단계(판단)가 끝날 때마다 자동으로 쌓입니다.
    </div>

    <div class="og-grid">
      <div class="t-card graph-card">
        <div class="t-head">
          <h2>이웃 그래프 <span class="t-sub">{{ graph ? `${centerLabel} · 노드 ${graph.nodes.length} · 관계 ${graph.edges.length}` : '' }}</span></h2>
          <div class="t-actions">
            <label class="t-small t-muted local-toggle"><input type="checkbox" v-model="showLocal" @change="draw" /> 보고서 내부 노드</label>
            <div class="search">
              <input v-model.trim="q" class="t-input" placeholder="종목·개념·테마 검색" aria-label="온톨로지 검색"
                @input="onSearch" @keydown.enter="pickFirst" @keydown.esc="results = []" />
              <ul v-if="results.length" class="results" role="listbox">
                <li v-for="r in results" :key="r.id" role="option" tabindex="0" @click="center(r.id)" @keydown.enter="center(r.id)">
                  <i :style="{ background: colorOf(r) }"></i>{{ r.label }}<span class="t-muted t-small"> {{ catLabel(r) }} · {{ r.id }}</span>
                </li>
              </ul>
            </div>
            <div class="seg" role="group" aria-label="탐색 깊이">
              <button :class="{ on: depth === 1 }" @click="setDepth(1)">1단계</button>
              <button :class="{ on: depth === 2 }" @click="setDepth(2)">2단계</button>
            </div>
          </div>
        </div>
        <div class="graph-wrap" ref="wrap">
          <svg ref="svg" class="graph" role="img" :aria-label="`${centerLabel} 중심 이웃 그래프`"></svg>
          <div v-if="tip" class="t-tooltip" :style="{ left: tip.x + 'px', top: tip.y + 'px' }">
            <b>{{ tip.label }}</b><br /><span class="t-muted">{{ tip.cat }} · {{ tip.id }}</span>
          </div>
          <div v-if="!graph || !graph.nodes.length" class="t-empty overlay">{{ loadingGraph ? '불러오는 중…' : '검색하거나 아래 공유 개념을 눌러 그래프를 여세요' }}</div>
        </div>
        <div class="t-legend">
          <span v-for="(c, k) in CATS" :key="k"><i :style="{ background: c.color }"></i>{{ c.label }}</span>
          <span class="t-muted">· 클릭 = 상세 · 두 번 클릭 = 중심 이동 · 끌어서 배치 · 휠 = 확대</span>
        </div>
      </div>

      <div class="t-card detail-card">
        <div class="t-head"><h2>상세</h2><span v-if="sel" class="t-pill"><i :style="{ background: colorOf(sel.node) }"></i>{{ catLabel(sel.node) }}</span></div>
        <template v-if="sel">
          <div class="d-title">{{ sel.node.label }}</div>
          <div class="t-mono t-small t-muted iri">wa:{{ sel.node.id }}</div>
          <div class="d-acts">
            <button v-if="sel.node.id !== centerIri" class="t-btn sm" @click="center(sel.node.id)">이 노드를 중심으로</button>
            <button v-if="reportIdOf(sel.node)" class="t-btn sm" @click="openReport(sel.node)">{{ sel.node.type === 'Prediction' ? '판단 열기' : '보고서 열기' }}</button>
            <button v-if="tickerOf(sel.node)" class="t-btn sm ghost" @click="$router.push({ path: '/reports', query: { q: tickerOf(sel.node) } })">보관함</button>
          </div>
          <table class="t-table props" v-if="sel.literals.length">
            <tbody>
              <tr v-for="(l, i) in sel.literals" :key="i"><td class="t-muted">{{ PROP_KO[l.p] || l.p }}</td><td>{{ l.o }}</td></tr>
            </tbody>
          </table>
          <div v-if="sel.history && sel.history.length" class="hist">
            <div class="t-small t-muted sec">새 분석에 들어가는 과거 맥락 (H#)</div>
            <div v-for="(h, i) in sel.history" :key="i" class="h-line"><b class="t-mono">H{{ i + 1 }}</b> {{ h.text }}</div>
          </div>
          <div class="t-small t-muted sec">관계 {{ sel.edges.length }}개</div>
          <ul class="rels">
            <li v-for="(e, i) in sel.edges.slice(0, 60)" :key="i" tabindex="0" @click="select(e.other)" @keydown.enter="select(e.other)">
              <span class="t-mono t-small pred">{{ e.out ? '→' : '←' }} {{ e.p }}</span>
              <i :style="{ background: colorOf(e.otherNode) }"></i>{{ e.otherNode.label }}
            </li>
          </ul>
        </template>
        <div v-else class="t-empty">그래프에서 노드를 누르면 속성·관계가 여기에 나옵니다</div>
      </div>
    </div>

    <div class="t-card" v-if="st && st.shared_concepts.length">
      <div class="t-head"><h2>종목을 잇는 공유 개념·키워드</h2><span class="t-sub">두 종목 이상의 분석에 함께 등장 — 숫자는 종목 수, 눌러서 연결 보기</span></div>
      <div class="chips">
        <button v-for="c in st.shared_concepts" :key="c.id" class="chip" @click="center(c.id)">
          {{ c.label }} <span class="t-mono">{{ c.stocks }}</span>
        </button>
      </div>
    </div>

    <div class="t-grid2">
      <div class="t-card">
        <div class="t-head">
          <h2>SHACL 검증 <span class="t-sub">종목·보고서·판단·ADR 형태 규칙</span></h2>
          <button class="t-btn sm" :disabled="busy" @click="doValidate">{{ busy === 'validate' ? '검증 중…' : '검증 실행' }}</button>
        </div>
        <template v-if="val">
          <div class="val-head">
            <span class="t-pill" :class="val.conforms ? 'ok' : 'err'"><i></i>{{ val.conforms ? '규칙 모두 충족' : `위반 ${val.count}건` }}</span>
            <span class="t-small t-muted">내장 규칙{{ val.pyshacl ? ' + pyshacl 표준 검증' : '' }}</span>
            <span v-if="val.pyshacl && !val.pyshacl.error" class="t-pill" :class="val.pyshacl.conforms ? 'ok' : 'err'"><i></i>pyshacl {{ val.pyshacl.conforms ? '통과' : '위반' }}</span>
          </div>
          <div class="t-scroll" v-if="val.violations.length">
            <table class="t-table">
              <thead><tr><th>대상</th><th>규칙</th><th>경로</th><th>내용</th></tr></thead>
              <tbody>
                <tr v-for="(v, i) in val.violations.slice(0, 50)" :key="i">
                  <td class="t-mono t-small">{{ v.focus }}</td><td>{{ v.shape }}</td><td class="t-mono t-small">{{ v.path }}</td><td>{{ v.message }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <details v-if="val.pyshacl && (val.pyshacl.report || val.pyshacl.error)" class="pyshacl">
            <summary class="t-small">pyshacl 보고서</summary>
            <pre>{{ val.pyshacl.report || val.pyshacl.error }}</pre>
          </details>
        </template>
        <p v-else class="t-small t-muted">종목코드 6자리·보고서당 대상 종목 1개·투자의견/전략 값·방향 확률 합 100%·ADR 확신도 0~100·술어 lowerCamelCase·끊어진 참조를 확인합니다. pyshacl 이 있으면 shapes.ttl 로 표준 SHACL 검증도 함께 돌립니다.</p>
      </div>

      <div class="t-card">
        <div class="t-head">
          <h2>SPARQL <span class="t-sub">읽기 전용 · prefix wa: 자동</span></h2>
          <div class="t-actions">
            <select class="t-select" v-model="exampleIdx" aria-label="예제 질의" @change="useExample">
              <option :value="-1">예제 질의…</option>
              <option v-for="(e, i) in EXAMPLES" :key="i" :value="i">{{ e.label }}</option>
            </select>
            <button class="t-btn sm primary" :disabled="busy || !query.trim()" @click="doSparql">{{ busy === 'sparql' ? '실행 중…' : '실행' }}</button>
          </div>
        </div>
        <div v-if="st && !st.rdflib" class="t-banner info">
          SPARQL 은 rdflib 이 있어야 합니다.
          <button class="t-btn sm" :disabled="!!installTask" @click="doInstall">{{ installTask ? installMsg || '설치 중…' : '온톨로지 도구 설치 (이 PC)' }}</button>
        </div>
        <textarea v-model="query" class="sparql" spellcheck="false" aria-label="SPARQL 질의" @keydown.ctrl.enter="doSparql"></textarea>
        <div v-if="sq && sq.type === 'ASK'" class="t-banner info">결과: {{ sq.answer ? '참 (true)' : '거짓 (false)' }}</div>
        <pre v-else-if="sq && sq.turtle" class="ttl">{{ sq.turtle }}</pre>
        <div v-else-if="sq" class="t-scroll sq">
          <table class="t-table">
            <thead><tr><th v-for="c in sq.columns" :key="c">{{ c }}</th></tr></thead>
            <tbody>
              <tr v-for="(r, i) in sq.rows" :key="i"><td v-for="(x, j) in r" :key="j" class="t-small">{{ x ?? '' }}</td></tr>
              <tr v-if="!sq.rows.length"><td :colspan="sq.columns.length || 1" class="t-empty">결과 없음</td></tr>
            </tbody>
          </table>
          <div class="t-small t-muted">{{ sq.rows.length }}행{{ sq.truncated ? ' (500행까지 표시)' : '' }}</div>
        </div>
      </div>
    </div>
  </AppShell>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as d3 from 'd3'
import AppShell from '../components/AppShell.vue'
import '../assets/terminal-ui.css'
import { getOntologyStats, searchOntology, getOntologyNode, getOntologyHistory, backfillOntology,
  validateOntology, runSparql, ttlUrl } from '../api/ontology.js'
import { installPackages } from '../api/admin.js'
import { getTask } from '../api/stock.js'

const route = useRoute()
const router = useRouter()
const st = ref(null)
const error = ref('')
const note = ref('')
const busy = ref('')
const q = ref('')
const results = ref([])
const depth = ref(1)
const graph = ref(null)
const centerIri = ref('')
const loadingGraph = ref(false)
const sel = ref(null)
const showLocal = ref(false)          // 지표·신호·뉴스 같은 보고서 내부 노드는 기본으로 숨김 (종목·개념·테마·보고서 관계가 잘 보이도록)
const tip = ref(null)
const val = ref(null)
const query = ref('')
const sq = ref(null)
const exampleIdx = ref(-1)
const installTask = ref('')
const installMsg = ref('')
const wrap = ref(null)
const svg = ref(null)
let sim = null
let ro = null
let searchTimer = null

// 검증된 범주형 순서 (어두운 배경 #0f1419 기준 CVD 분리 확인) + 보고서 내부 노드는 회색
const CATS = {
  stock: { label: '종목', color: '#3987e5' },
  concept: { label: '공유 개념·키워드', color: '#199e70' },
  theme: { label: '테마', color: '#c98500' },
  report: { label: '보고서', color: '#9085e9' },
  prediction: { label: '판단', color: '#d55181' },
  adr: { label: 'ADR', color: '#d95926' },
  local: { label: '보고서 내부 (지표·신호·사건·CQ)', color: '#5b6577' },
}
const RADIUS = { stock: 10, concept: 7.5, theme: 8.5, report: 6.5, prediction: 7, adr: 6, local: 4.5 }
const PROP_KO = {
  ticker: '종목코드', name: '종목명', label: '라벨', value: '값', rating: '투자의견', targetPrice: '목표주가', basePrice: '기준가',
  upsidePct: '상승여력(%)', valuationMethod: '밸류에이션', asOf: '기준일', createdAt: '작성', verdict: '전략', probUp: '상승 확률(%)',
  probFlat: '보합 확률(%)', probDown: '하락 확률(%)', entryPrice: '진입가', planTarget: '계획 목표가', stopPrice: '손절가',
  positionPct: '비중(%)', horizon: '기간', decidedAt: '판단 시각', ledgerStatus: '원장 상태', realizedReturnPct: '실현 수익률(%)',
  realizedDirection: '실현 방향', strategyHit: '전략 적중', planOutcome: '계획 결과', title: '제목', decision: '결정',
  confidence: '확신(%)', status: '상태', question: '질문', answer: '답', keyword: '키워드', featuredOn: '테마 선정일',
}
const EXAMPLES = [
  { label: '종목별 보고서 수', q: `SELECT ?name (COUNT(DISTINCT ?r) AS ?reports) WHERE {
  ?r a wa:Report ; wa:hasSubject ?s .
  ?s wa:name ?name .
} GROUP BY ?name ORDER BY DESC(?reports)` },
  { label: '두 종목 이상에 등장한 개념', q: `SELECT ?label (COUNT(DISTINCT ?s) AS ?stocks) WHERE {
  ?s a wa:Stock ; wa:mentions ?c .
  ?c wa:label ?label .
} GROUP BY ?label HAVING (COUNT(DISTINCT ?s) >= 2) ORDER BY DESC(?stocks)` },
  { label: '판단과 실제 결과', q: `SELECT ?name ?verdict ?probUp ?ret ?hit WHERE {
  ?p a wa:Prediction ; wa:forReport ?r ; wa:verdict ?verdict .
  ?r wa:hasSubject/wa:name ?name .
  OPTIONAL { ?p wa:probUp ?probUp }
  OPTIONAL { ?p wa:realizedReturnPct ?ret }
  OPTIONAL { ?p wa:strategyHit ?hit }
} ORDER BY ?name` },
  { label: '테마별 편입 종목', q: `SELECT ?theme ?name WHERE {
  ?t a wa:Theme ; wa:label ?theme ; wa:hasMember ?s .
  ?s wa:name ?name .
} ORDER BY ?theme ?name` },
  { label: '확신 70% 이상 ADR', q: `SELECT ?name ?title ?conf WHERE {
  ?a a wa:ADR ; wa:title ?title ; wa:confidence ?conf ; wa:inReport ?r .
  ?r wa:hasSubject/wa:name ?name .
  FILTER(?conf >= 70)
} ORDER BY DESC(?conf) LIMIT 30` },
]

const fmt = (v) => (v == null ? '-' : Number(v).toLocaleString('ko-KR'))
const sharedMulti = computed(() => (st.value?.shared_concepts || []).length)
const centerLabel = computed(() => graph.value?.nodes.find(n => n.id === centerIri.value)?.label || centerIri.value)

function cat(n) {
  if (!n) return 'local'
  if (n.type === 'Stock') return 'stock'
  if (n.type === 'Theme') return 'theme'
  if (n.type === 'Report') return 'report'
  if (n.type === 'Prediction') return 'prediction'
  if (n.type === 'ADR') return 'adr'
  if (String(n.id).startsWith('concept/') || String(n.id).startsWith('keyword/')) return 'concept'
  return 'local'
}
const colorOf = (n) => CATS[cat(n)].color
const catLabel = (n) => (cat(n) === 'local' ? (n.type || '노드') : n.type === 'Keyword' ? '키워드' : cat(n) === 'concept' ? '공유 개념' : CATS[cat(n)].label)
const reportIdOf = (n) => { const m = /^(report|prediction)\/(.+)$/.exec(n?.id || ''); return m ? m[2] : null }
const tickerOf = (n) => { const m = /^stock\/(\w{6})$/.exec(n?.id || ''); return m ? m[1] : null }
function openReport(n) {
  router.push({ path: '/stock', query: { report: reportIdOf(n), step: n.type === 'Prediction' ? 4 : 2 } })
}

async function loadStats() {
  try { st.value = (await getOntologyStats()).data } catch (e) { error.value = `통계를 불러오지 못했습니다: ${e.message}` }
}

function onSearch() {
  clearTimeout(searchTimer)
  if (!q.value) { results.value = []; return }
  searchTimer = setTimeout(async () => {
    try { results.value = (await searchOntology(q.value)).data.slice(0, 12) } catch { results.value = [] }
  }, 220)
}
function pickFirst() { if (results.value[0]) center(results.value[0].id) }

async function center(iri) {
  results.value = []
  error.value = ''
  centerIri.value = iri
  loadingGraph.value = true
  try {
    graph.value = (await getOntologyNode(iri, depth.value)).data
    router.replace({ query: { ...route.query, iri } })
    await nextTick()
    draw()
    await select(iri)
  } catch (e) {
    error.value = `그래프를 불러오지 못했습니다: ${e.message}`
  } finally { loadingGraph.value = false }
}
function setDepth(d) { depth.value = d; if (centerIri.value) center(centerIri.value) }

async function select(iri) {
  try {
    const nb = iri === centerIri.value && depth.value === 1 ? graph.value : (await getOntologyNode(iri, 1)).data
    const byId = Object.fromEntries(nb.nodes.map(n => [n.id, n]))
    const node = byId[iri] || graph.value?.nodes.find(n => n.id === iri) || { id: iri, label: iri }
    const edges = nb.edges.filter(e => e.s === iri || e.o === iri).map(e => {
      const other = e.s === iri ? e.o : e.s
      return { p: e.p, out: e.s === iri, other, otherNode: byId[other] || { id: other, label: other } }
    }).sort((a, b) => (cat(a.otherNode) === 'local') - (cat(b.otherNode) === 'local'))   // 보고서를 넘는 관계 먼저
    const seen = new Set()
    const literals = nb.literals.filter(l => { const k = l.p + '|' + l.o; if (seen.has(k)) return false; seen.add(k); return true })
    sel.value = { node, edges, literals, history: null }
    highlight(iri)
    const t = tickerOf(node)
    if (t) {
      const h = (await getOntologyHistory(t)).data
      if (sel.value?.node.id === iri) sel.value = { ...sel.value, history: h }
    }
  } catch (e) { error.value = `상세를 불러오지 못했습니다: ${e.message}` }
}

// ─────────────────────────────────────────── d3-force 그래프
let gSel = null
function draw() {
  if (sim) { sim.stop(); sim = null }
  const el = svg.value
  if (!el || !graph.value) return
  const W = wrap.value.clientWidth || 800
  const H = wrap.value.clientHeight || 520
  const root = d3.select(el).attr('viewBox', `0 0 ${W} ${H}`)
  root.selectAll('*').remove()
  const nodes = graph.value.nodes.map(n => ({ ...n, c: cat(n) }))
    .filter(n => showLocal.value || n.c !== 'local' || n.id === centerIri.value)
  const ids = new Set(nodes.map(n => n.id))
  const links = graph.value.edges.filter(e => ids.has(e.s) && ids.has(e.o)).map(e => ({ source: e.s, target: e.o, p: e.p }))
  const deg = {}
  links.forEach(l => { deg[l.source] = (deg[l.source] || 0) + 1; deg[l.target] = (deg[l.target] || 0) + 1 })
  const many = nodes.length > 70
  const g = root.append('g')
  gSel = g
  root.call(d3.zoom().scaleExtent([0.25, 4]).on('zoom', (ev) => g.attr('transform', ev.transform)))
  root.on('dblclick.zoom', null)

  const link = g.append('g').attr('class', 'links').selectAll('line').data(links).join('line')
    .attr('stroke', '#39445a').attr('stroke-width', 1).attr('stroke-opacity', 0.75)
  const node = g.append('g').attr('class', 'nodes').selectAll('g').data(nodes).join('g').attr('class', 'node')
    .style('cursor', 'pointer')
  node.append('circle')
    .attr('r', d => RADIUS[d.c] + Math.min(5, Math.sqrt(deg[d.id] || 0)) + (d.id === centerIri.value ? 3 : 0))
    .attr('fill', d => CATS[d.c].color)
    .attr('stroke', d => (d.id === centerIri.value ? '#e2e8f0' : '#0f1419'))
    .attr('stroke-width', d => (d.id === centerIri.value ? 2.5 : 2))
  node.filter(d => d.id === centerIri.value || d.c === 'stock' || d.c === 'theme' || (!many && (d.c === 'concept' || d.c === 'prediction')))
    .append('text').text(d => (d.label.length > 18 ? d.label.slice(0, 17) + '…' : d.label))
    .attr('x', d => RADIUS[d.c] + 6).attr('y', 4)
    .attr('fill', '#c9d4e0').attr('font-size', 11).attr('paint-order', 'stroke')
    .attr('stroke', '#0f1419').attr('stroke-width', 3).attr('stroke-linejoin', 'round').style('pointer-events', 'none')

  node.on('mouseenter', (ev, d) => {
    const r = wrap.value.getBoundingClientRect()
    tip.value = { x: Math.min(ev.clientX - r.left + 12, r.width - 220), y: ev.clientY - r.top + 12, label: d.label, cat: catLabel(d), id: d.id }
    link.attr('stroke', l => (l.source.id === d.id || l.target.id === d.id ? '#8aa0c0' : '#39445a'))
  }).on('mouseleave', () => { tip.value = null; link.attr('stroke', '#39445a') })
    .on('click', (ev, d) => { ev.stopPropagation(); select(d.id) })
    .on('dblclick', (ev, d) => { ev.stopPropagation(); center(d.id) })
    .call(d3.drag()
      .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y })
      .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y })
      .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null }))

  const c = nodes.find(n => n.id === centerIri.value)
  if (c) { c.fx = W / 2; c.fy = H / 2 }
  sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(l => (l.source.c === 'local' || l.target.c === 'local' ? 38 : 70)).strength(0.5))
    .force('charge', d3.forceManyBody().strength(many ? -70 : -160))
    .force('collide', d3.forceCollide().radius(d => RADIUS[d.c] + 6))
    .force('x', d3.forceX(W / 2).strength(0.04))
    .force('y', d3.forceY(H / 2).strength(0.06))
    .on('tick', () => {
      link.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y)
      node.attr('transform', d => `translate(${d.x},${d.y})`)
    })
  setTimeout(() => { if (c) { c.fx = null; c.fy = null } }, 1500)
}
function highlight(iri) {
  if (!gSel) return
  gSel.selectAll('g.node circle').attr('stroke', d => (d.id === iri ? '#fbbf24' : d.id === centerIri.value ? '#e2e8f0' : '#0f1419'))
}

// ─────────────────────────────────────────── 적재 · 검증 · SPARQL · 설치
async function doBackfill() {
  busy.value = 'backfill'; error.value = ''; note.value = ''
  try {
    const r = (await backfillOntology()).data
    note.value = `적재 완료 — 새 보고서 ${r.ingested}개 · 변경 없음 ${r.skipped}개${r.failed ? ` · 실패 ${r.failed}개` : ''} · 테마 연결 ${r.theme_links}개 · 채점 결과 ${r.outcomes}건`
    await loadStats()
    if (!centerIri.value) startCenter()
    else center(centerIri.value)
  } catch (e) { error.value = `적재 실패: ${e.message}` } finally { busy.value = '' }
}
async function doValidate() {
  busy.value = 'validate'; error.value = ''
  try { val.value = (await validateOntology()).data } catch (e) { error.value = `검증 실패: ${e.message}` } finally { busy.value = '' }
}
function useExample() { if (exampleIdx.value >= 0) query.value = EXAMPLES[exampleIdx.value].q }
async function doSparql() {
  if (!query.value.trim()) return
  busy.value = 'sparql'; error.value = ''
  try { sq.value = (await runSparql(query.value)).data } catch (e) { error.value = `SPARQL: ${e.message}`; sq.value = null } finally { busy.value = '' }
}
async function doInstall() {
  error.value = ''
  try {
    installTask.value = (await installPackages('ontology')).data.task_id
    const poll = async () => {
      const t = (await getTask(installTask.value)).data
      installMsg.value = t.message ? t.message.slice(0, 60) : ''
      if (t.status === 'completed') { installTask.value = ''; note.value = 'rdflib·pyshacl 설치 완료 — SPARQL·표준 SHACL 을 쓸 수 있습니다'; loadStats(); return }
      if (t.status === 'failed') { installTask.value = ''; error.value = `설치 실패: ${t.error || t.message}`; return }
      setTimeout(poll, 2500)
    }
    setTimeout(poll, 2000)
  } catch (e) { installTask.value = ''; error.value = `설치를 시작하지 못했습니다: ${e?.message || e} (설치는 이 PC 에서만 가능합니다)` }
}

function startCenter() {
  const iri = route.query.iri || (route.query.ticker ? `stock/${route.query.ticker}` : '')
    || st.value?.shared_concepts?.[0]?.id
  if (iri) center(String(iri))
}

onMounted(async () => {
  query.value = EXAMPLES[1].q
  await loadStats()
  startCenter()
  let lastW = 0, rt = null
  ro = new ResizeObserver(() => {
    const w = wrap.value?.clientWidth || 0
    if (!graph.value || Math.abs(w - lastW) < 24) return
    lastW = w
    clearTimeout(rt)
    rt = setTimeout(draw, 180)
  })
  if (wrap.value) ro.observe(wrap.value)
})
onBeforeUnmount(() => { if (sim) sim.stop(); if (ro) ro.disconnect(); clearTimeout(searchTimer) })
</script>

<style scoped>
.og-grid { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 14px; margin-top: 14px; }
.og-grid .t-card + .t-card { margin-top: 0; }
.graph-wrap { position: relative; height: 520px; border: 1px solid var(--subtle); border-radius: 8px; background: #0c1117; overflow: hidden; }
.graph { width: 100%; height: 100%; display: block; }
.overlay { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.small-b { font-size: 13px !important; display: flex; gap: 6px; }
.search { position: relative; }
.search .t-input { width: 220px; }
.results { position: absolute; top: 36px; left: 0; z-index: 10; width: 360px; max-height: 320px; overflow: auto; margin: 0; padding: 4px;
  list-style: none; background: #0b0f14; border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.45); }
.results li { padding: 6px 8px; border-radius: 5px; cursor: pointer; display: flex; align-items: center; gap: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.results li:hover, .results li:focus { background: var(--subtle); outline: none; }
.results i, .rels i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.local-toggle { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.seg button { font: inherit; font-size: 12px; background: var(--panel-2); color: var(--muted); border: 0; padding: 0 10px; height: 28px; cursor: pointer; }
.seg button.on { background: var(--subtle); color: var(--text); }
.detail-card { max-height: 640px; overflow: auto; }
.d-title { font-size: 15px; font-weight: 700; margin-bottom: 2px; word-break: keep-all; }
.iri { word-break: break-all; margin-bottom: 8px; }
.d-acts { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
.props td { padding: 4px 6px; font-size: 12px; }
.props td:first-child { width: 42%; white-space: nowrap; }
.sec { margin: 12px 0 6px; font-weight: 600; }
.hist { margin-top: 6px; }
.h-line { font-size: 12px; color: var(--text-2); padding: 4px 0; border-bottom: 1px dashed var(--subtle); }
.h-line b { color: #9085e9; margin-right: 4px; }
.rels { list-style: none; margin: 0; padding: 0; }
.rels li { display: flex; align-items: center; gap: 6px; padding: 4px 4px; border-radius: 5px; cursor: pointer; font-size: 12px; color: var(--text-2); }
.rels li:hover, .rels li:focus { background: var(--subtle); outline: none; }
.rels .pred { color: var(--muted); min-width: 110px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font: inherit; font-size: 12px; background: rgba(25, 158, 112, .1); border: 1px solid rgba(25, 158, 112, .35); color: #a7f3d0; border-radius: 99px; padding: 3px 10px; cursor: pointer; }
.chip span { color: #6ee7b7; margin-left: 4px; }
.chip:hover { background: rgba(25, 158, 112, .2); }
.val-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; }
.pyshacl pre, .ttl { white-space: pre-wrap; font-family: var(--mono); font-size: 11px; color: var(--text-2); background: #0c1117; border: 1px solid var(--subtle); border-radius: 6px; padding: 8px; max-height: 260px; overflow: auto; }
.sparql { width: 100%; min-height: 150px; resize: vertical; font-family: var(--mono); font-size: 12px; line-height: 1.5; color: var(--text); background: #0c1117;
  border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; box-sizing: border-box; margin-bottom: 8px; }
.sparql:focus { outline: 2px solid var(--primary); outline-offset: 1px; }
.sq { max-height: 320px; overflow: auto; }
a.t-btn { text-decoration: none; }
@media (max-width: 1100px) { .og-grid { grid-template-columns: 1fr; } .detail-card { max-height: none; } }
@media (max-width: 600px) { .graph-wrap { height: 400px; } .search .t-input { width: 160px; } .results { width: 280px; } }
</style>
