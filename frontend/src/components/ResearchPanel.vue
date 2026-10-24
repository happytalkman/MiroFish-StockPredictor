<template>
  <div class="rp">
    <ProgressModal
      :show="pm.show"
      title="2단계 리서치 진행 중"
      subtitle="Grok 이 조사 계획 → 뉴스·공시·해외 검색 → 사실 추출 → 부족분 재검색(리플렉션) → 감성·종합 순으로 진행합니다."
      done-title="리서치가 완료되었습니다"
      :stages="pm.stages" :progress="pm.progress" :message="pm.message" :logs="pm.logs"
      :status="pm.status" :error="pm.error" :started-at="pm.startedAt" :expected-sec="150"
      @close="pmClose" @retry="pmClose(); rerun(true)"
    />

    <div v-if="loading && !r" class="rp-loading">
      <div class="spinner"></div>
      <span>리서치 결과를 불러오는 중…</span>
    </div>

    <div v-else-if="!r" class="rp-empty">
      <div class="t">리서치 결과가 없습니다</div>
      <div class="s">{{ error || '최근 뉴스·공시·해외 보도·커뮤니티를 조사해 출처가 달린 근거를 만듭니다 (1~3분 소요).' }}</div>
      <button class="btn primary" type="button" :disabled="running" @click="rerun(false)">리서치 실행</button>
    </div>

    <template v-else>
      <!-- 헤더 -->
      <div class="rp-head">
        <div class="rp-head-l">
          <div class="cap">2단계 리서치 · {{ r.company_name }} ({{ r.ticker }})<template v-if="r.company_name_en"> · {{ r.company_name_en }}</template></div>
          <div class="chips">
            <span class="chip">발견 {{ findings.length }}건</span>
            <span class="chip">출처 {{ r.sources_count ?? 0 }}곳</span>
            <span class="chip">라운드 {{ (r.rounds || []).length }}회</span>
            <span class="chip">{{ r.elapsed_sec ?? '-' }}초</span>
            <span class="chip dim">감성 엔진 · {{ engineLabel }}</span>
            <span v-if="r.llm" class="chip dim">{{ r.llm }}</span>
          </div>
        </div>
        <div class="rp-head-r">
          <span class="muted small">{{ createdText }}</span>
          <button class="btn primary sm" type="button" :disabled="running" @click="rerun(true)">↻ 리서치 다시 하기</button>
        </div>
      </div>
      <div v-if="error" class="rp-error">{{ error }}</div>

      <!-- 감성 게이지 -->
      <div class="card">
        <div class="card-title">감성 지표 <span class="src">−1 부정 ~ +1 긍정 · 주가 방향이 아닌 글의 논조</span></div>
        <div class="gauges">
          <div v-for="g in gauges" :key="g.key" class="gauge">
            <div class="g-top">
              <span class="g-label">{{ g.label }}</span>
              <b :class="g.available ? toneClass(g.score) : 'neu'">{{ g.available ? signed(g.score) : '-' }}</b>
            </div>
            <div class="g-track">
              <div class="g-mid"></div>
              <div v-if="g.available" class="g-fill" :class="toneClass(g.score)" :style="fillStyle(g.score)"></div>
            </div>
            <div class="g-sub">{{ g.sub }}</div>
          </div>
        </div>
      </div>

      <!-- 요약 -->
      <div v-if="r.summary" class="card">
        <div class="card-title">리서치 요약 <span class="src">[R#] 을 누르면 해당 출처로 이동합니다</span></div>
        <p class="summary">
          <template v-for="(seg, i) in segs(r.summary)" :key="i">
            <button v-if="seg.ref" class="ref" type="button" @click="goRef(seg.ref)">{{ seg.ref }}</button>
            <span v-else>{{ seg.text }}</span>
          </template>
        </p>
      </div>

      <div class="grid2">
        <div class="card">
          <div class="card-title">핵심 포인트</div>
          <ul v-if="(r.key_points || []).length" class="lst">
            <li v-for="(k, i) in r.key_points" :key="i">
              {{ k.text }}
              <button v-for="ref in validRefs(k.refs)" :key="ref" class="ref" type="button" @click="goRef(ref)">{{ ref }}</button>
            </li>
          </ul>
          <p v-else class="muted small">핵심 포인트가 없습니다.</p>
        </div>
        <div class="card">
          <div class="card-title">리스크</div>
          <ul v-if="(r.risks || []).length" class="lst risk">
            <li v-for="(k, i) in r.risks" :key="i">
              {{ k.text }}
              <button v-for="ref in validRefs(k.refs)" :key="ref" class="ref" type="button" @click="goRef(ref)">{{ ref }}</button>
            </li>
          </ul>
          <p v-else class="muted small">확인된 리스크 보도가 없습니다.</p>
        </div>
      </div>

      <!-- 발견 (종류별) -->
      <div class="card">
        <div class="card-title">발견 {{ findings.length }}건 <span class="src">사실 1개 = 출처 1개</span></div>
        <p v-if="!findings.length" class="muted small">수집된 발견이 없습니다. 아래 ‘아직 확인하지 못한 정보’를 확인하세요.</p>
        <div v-for="grp in groups" :key="grp.kind" class="grp">
          <div class="grp-title">{{ grp.label }} <span class="cnt">{{ grp.items.length }}</span></div>
          <div
            v-for="f in visibleOf(grp)" :id="domId(f.id)" :key="f.id"
            class="finding" :class="{ flash: flash === f.id }"
          >
            <div class="f-top">
              <span class="rid">{{ f.id }}</span>
              <span class="claim">{{ f.claim }}</span>
            </div>
            <div v-if="f.detail && f.detail !== f.claim" class="detail">{{ f.detail }}</div>
            <div class="f-meta">
              <span>{{ f.date || '날짜 없음' }}</span>
              <span class="pub">{{ (f.source && f.source.publisher) || '-' }}</span>
              <span v-if="f.topic" class="topic">{{ f.topic }}</span>
              <span class="schip" :class="sentClass(f.sentiment)">{{ sentLabel(f.sentiment) }}</span>
              <span v-if="f.importance" class="imp">중요도 {{ f.importance }}</span>
              <a
                v-if="safeUrl(f.source && f.source.url)" class="src-link"
                :href="safeUrl(f.source.url)" target="_blank" rel="noopener noreferrer"
                :title="(f.source && f.source.title) || ''"
              >원문 ↗</a>
            </div>
          </div>
          <button
            v-if="grp.items.length > LIMIT && !expanded[grp.kind]" class="more" type="button"
            @click="expanded[grp.kind] = true"
          >{{ grp.items.length - LIMIT }}건 더 보기</button>
        </div>
      </div>

      <!-- 리서치 과정 -->
      <div class="card">
        <div class="card-title">리서치 과정 <span class="src">계획 → 검색 → 사실 추출 → 리플렉션</span></div>
        <details v-if="(r.plan || []).length" class="plan">
          <summary>조사 질문 {{ r.plan.length }}개</summary>
          <ol>
            <li v-for="(q, i) in r.plan" :key="i">{{ q }}</li>
          </ol>
        </details>
        <ol class="timeline">
          <li v-for="rd in r.rounds || []" :key="rd.round" class="tl-item">
            <div class="tl-dot" :class="rd.type === 'reflection' ? 'refl' : 'init'"></div>
            <div class="tl-body">
              <div class="tl-head">
                <b>{{ rd.round }}라운드</b>
                <span class="muted">{{ rd.type === 'reflection' ? '리플렉션 재검색' : '초기 검색' }}</span>
                <span class="chip">새 발견 {{ rd.new_findings ?? 0 }}건</span>
              </div>
              <div v-if="(rd.gaps || []).length" class="gaps">
                <span class="gap-cap">부족했던 부분</span>
                <span v-for="(g, i) in rd.gaps" :key="i" class="gap">{{ g }}</span>
              </div>
              <details class="qs">
                <summary>
                  검색어 {{ (rd.queries || []).length }}개<template v-if="rd.collected"> · 수집 뉴스 {{ rd.collected.news || 0 }} · 공시 {{ rd.collected.disclosure || 0 }} · 해외 {{ rd.collected.global || 0 }}</template>
                </summary>
                <div class="q-list">
                  <span v-for="(q, i) in rd.queries || []" :key="i" class="q">{{ q }}</span>
                </div>
                <div v-if="(rd.errors || []).length" class="errs">
                  <div v-for="(e, i) in rd.errors" :key="i">⚠ {{ e }}</div>
                </div>
              </details>
            </div>
          </li>
        </ol>
        <div v-if="(r.gaps_remaining || []).length" class="remain">
          <div class="gap-cap">아직 확인하지 못한 정보</div>
          <ul>
            <li v-for="(g, i) in r.gaps_remaining" :key="i">{{ g }}</li>
          </ul>
        </div>
      </div>

      <!-- 커뮤니티 -->
      <div class="card">
        <div class="card-title">커뮤니티 심리 <span class="src">{{ comSources }}</span></div>
        <div class="com-disc">⚠ 커뮤니티 글은 참고용 심리 지표이며 사실이 아닙니다.</div>
        <div v-for="(p, i) in visiblePosts" :key="i" class="post">
          <span class="schip" :class="sentClass(p.sentiment)">{{ sentLabel(p.sentiment) }}</span>
          <div class="p-body">
            <a
              v-if="safeUrl(p.url)" class="p-title" :href="safeUrl(p.url)"
              target="_blank" rel="noopener noreferrer"
            >{{ p.title }}</a>
            <span v-else class="p-title">{{ p.title }}</span>
            <span class="p-meta">
              {{ srcLabel(p.source) }}<template v-if="p.date"> · {{ p.date }}</template><template v-if="p.views != null"> · 조회 {{ num(p.views) }}</template><template v-if="p.likes != null"> · 공감 {{ num(p.likes) }}</template><template v-if="p.dislikes != null"> · 비공감 {{ num(p.dislikes) }}</template>
            </span>
          </div>
        </div>
        <button
          v-if="posts.length > POST_LIMIT && !expanded.posts" class="more" type="button"
          @click="expanded.posts = true"
        >{{ posts.length - POST_LIMIT }}건 더 보기</button>
        <p v-if="!posts.length" class="muted small">수집된 커뮤니티 글이 없습니다.</p>
        <ul v-if="(com.notes || []).length" class="notes">
          <li v-for="(n, i) in com.notes" :key="i">{{ n }}</li>
        </ul>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import api from '../api/index.js'
import ProgressModal from './ProgressModal.vue'
import { useProgress } from '../composables/useProgress.js'

const props = defineProps({
  taskId: { type: String, required: true },        // 분석(analyze) 작업 ID
  research: { type: Object, default: null }        // 이미 가진 research.json (없으면 직접 조회)
})
const emit = defineEmits(['rerun'])

const r = ref(props.research)
const loading = ref(false)
const running = ref(false)
const error = ref('')
const flash = ref('')
const expanded = reactive({ news: false, disclosure: false, global: false, etc: false, posts: false })
const LIMIT = 8
const POST_LIMIT = 12
const uid = 'rp' + Math.random().toString(36).slice(2, 8)
let flashTimer = null

const { pm, start: pmStart, log: pmLog, stage: pmStage, done: pmDone, fail: pmFail, close: pmClose, follow } = useProgress()
const STAGES = [
  { key: 'plan', label: '조사 계획' },
  { key: 'search', label: '검색·사실 추출' },
  { key: 'reflect', label: '리플렉션 재검색' },
  { key: 'final', label: '감성·종합' }
]
const ORDER = STAGES.map(s => s.key)
const PHASE = { start: 'plan', plan: 'plan', search: 'search', summarize: 'search', reflect: 'reflect', sentiment: 'final', synthesize: 'final', done: 'done' }

const researchUrl = () => `/api/stock/research/${encodeURIComponent(props.taskId)}`

async function load() {
  if (!props.taskId) return
  loading.value = true
  error.value = ''
  try {
    const res = await api.get(researchUrl(), { timeout: 20000 })
    r.value = res.data || null
  } catch (e) {
    if (e.response?.status !== 404) error.value = e.response?.data?.error || e.message || '리서치 조회 실패'
    r.value = null
  } finally {
    loading.value = false
  }
}

async function rerun(force = true) {
  if (running.value || !props.taskId) return
  running.value = true
  error.value = ''
  try {
    const res = await api.post(researchUrl(), { force }, { timeout: 60000 })
    if (!res.data?.task_id) {
      r.value = res.data || null
      emit('rerun', r.value)
      return
    }
    pmStart(STAGES.map(s => ({ ...s, status: 'pending' })))
    pmLog('리서치 작업을 시작합니다')
    await follow(res.data.task_id, {
      onUpdate: (t) => {
        const d = t.progress_detail || {}
        let cur = PHASE[d.phase] || null
        if ((d.phase === 'search' || d.phase === 'summarize') && (d.round || 0) > 1) cur = 'reflect'
        if (!cur) return
        const idx = cur === 'done' ? ORDER.length : ORDER.indexOf(cur)
        ORDER.forEach((k, i) => pmStage(k, i < idx ? 'done' : i === idx ? 'running' : 'pending'))
      }
    })
    ORDER.forEach(k => pmStage(k, 'done'))
    await load()
    pmDone(`리서치 완료 — 발견 ${r.value?.findings?.length || 0}건 · 출처 ${r.value?.sources_count || 0}곳`)
    emit('rerun', r.value)
  } catch (e) {
    const msg = e.response?.data?.error || e.message || '리서치 실패'
    if (pm.show) pmFail(msg)
    error.value = msg
  } finally {
    running.value = false
  }
}

onMounted(() => { if (!r.value) load() })
watch(() => props.research, (v) => { if (v) r.value = v })
watch(() => props.taskId, () => { if (!props.research) load() })
onBeforeUnmount(() => clearTimeout(flashTimer))

// ─── 표시용 계산 ─────────────────────────────────────────────
const findings = computed(() => (r.value?.findings || []).filter(f => f && f.id))
const ids = computed(() => new Set(findings.value.map(f => f.id)))
const GROUPS = [
  { kind: 'news', label: '국내 뉴스' },
  { kind: 'disclosure', label: '공시' },
  { kind: 'global', label: '해외' }
]
const groups = computed(() => {
  const known = GROUPS.map(g => g.kind)
  const out = GROUPS.map(g => ({ ...g, items: findings.value.filter(f => f.kind === g.kind) }))
  out.push({ kind: 'etc', label: '기타', items: findings.value.filter(f => !known.includes(f.kind)) })
  return out.filter(g => g.items.length)
})
const visibleOf = (grp) => (expanded[grp.kind] ? grp.items : grp.items.slice(0, LIMIT))

const sent = computed(() => r.value?.sentiment || {})
const gauges = computed(() => {
  const n = sent.value.news || {}
  const c = sent.value.community || {}
  const sub = (x) => `긍정 ${x.pos || 0} · 중립 ${x.neu || 0} · 부정 ${x.neg || 0} (${x.n || 0}건)`
  const both = (n.n || 0) > 0 && (c.n || 0) > 0
  return [
    { key: 'news', label: '뉴스·공시·해외', score: Number(n.score) || 0, available: (n.n || 0) > 0, sub: sub(n) },
    { key: 'community', label: '커뮤니티', score: Number(c.score) || 0, available: (c.n || 0) > 0, sub: (c.n || 0) > 0 ? sub(c) : '수집된 글 없음' },
    {
      key: 'overall', label: '종합', score: Number(sent.value.overall_score) || 0,
      available: (n.n || 0) > 0 || (c.n || 0) > 0,
      sub: both ? '뉴스 70% + 커뮤니티 30%' : (n.n || 0) > 0 ? '뉴스 기준' : (c.n || 0) > 0 ? '커뮤니티 기준' : '데이터 없음'
    }
  ]
})
const ENGINE = { local: '로컬 모델', llm: 'Grok', lexicon: '감성 사전' }
const engineLabel = computed(() => ENGINE[sent.value.engine] || sent.value.engine || '-')
const createdText = computed(() => (r.value?.created_at ? String(r.value.created_at).replace('T', ' ') : ''))

const com = computed(() => r.value?.community || {})
const posts = computed(() => (com.value.posts || []).filter(p => p && p.title))
const visiblePosts = computed(() => (expanded.posts ? posts.value : posts.value.slice(0, POST_LIMIT)))
const SRC = { naver: '네이버 종목토론실', youtube: 'YouTube 댓글' }
const srcLabel = (s) => SRC[s] || s || '-'
const comSources = computed(() => (com.value.sources || []).map(srcLabel).join(' · ') || '소스 없음')

// ─── 형식 도우미 ─────────────────────────────────────────────
function safeUrl(u) {
  const s = String(u || '').trim()
  return /^https?:\/\/[^\s<>"']+$/i.test(s) ? s : ''
}
const clamp = (x) => Math.max(-1, Math.min(1, Number(x) || 0))
const signed = (v) => { const x = Number(v) || 0; return (x > 0 ? '+' : '') + x.toFixed(2) }
const toneClass = (v) => (v > 0.1 ? 'pos' : v < -0.1 ? 'neg' : 'neu')
function fillStyle(score) {
  const s = clamp(score)
  const w = Math.abs(s) * 50
  return { width: `${w}%`, left: s >= 0 ? '50%' : `${50 - w}%` }
}
const sentClass = (s) => ({ positive: 'pos', negative: 'neg' }[s?.label] || 'neu')
function sentLabel(s) {
  const lab = { positive: '긍정', negative: '부정', neutral: '중립' }[s?.label] || '중립'
  return s && s.score != null && s.label !== 'neutral' ? `${lab} ${signed(s.score)}` : lab
}
const num = (v) => { const x = Number(v); return Number.isFinite(x) ? x.toLocaleString('ko-KR') : String(v) }
const domId = (id) => `${uid}-${id}`
const validRefs = (refs) => (refs || []).filter(x => ids.value.has(x))

// "문장 [R3]." → [{text}, {ref:'R3'}, {text:'.'}] (없는 번호는 글자 그대로)
function segs(text) {
  const out = []
  const re = /\[(R\d{1,3})\]/g
  const s = String(text || '')
  let last = 0
  let m
  while ((m = re.exec(s)) !== null) {
    if (m.index > last) out.push({ text: s.slice(last, m.index) })
    out.push(ids.value.has(m[1]) ? { ref: m[1] } : { text: m[0] })
    last = m.index + m[0].length
  }
  if (last < s.length) out.push({ text: s.slice(last) })
  return out
}

async function goRef(id) {
  const f = findings.value.find(x => x.id === id)
  if (!f) return
  const grp = groups.value.find(g => g.items.includes(f))
  if (grp && grp.items.indexOf(f) >= LIMIT) expanded[grp.kind] = true
  await nextTick()
  const el = document.getElementById(domId(id))
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  flash.value = id
  clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { flash.value = '' }, 2200)
}
</script>

<style scoped>
.rp { display: flex; flex-direction: column; gap: 14px; min-width: 0; font-family: 'Noto Sans KR', 'Inter', sans-serif; }
.rp-loading { display: flex; gap: 12px; align-items: center; padding: 20px; background: #0f1419; border: 1px solid #2a3041; border-radius: 10px; color: #8892a4; font-size: 0.86rem; }
.spinner { width: 22px; height: 22px; border: 3px solid rgba(59,130,246,.2); border-top-color: #3b82f6; border-radius: 50%; animation: spin .8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.rp-empty { padding: 22px; background: #0f1419; border: 1px dashed #2a3041; border-radius: 10px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.rp-empty .t { color: #e2e8f0; font-weight: 700; }
.rp-empty .s { color: #8892a4; font-size: 0.83rem; line-height: 1.6; }
.rp-error { color: #fca5a5; background: rgba(239,68,68,.08); border: 1px solid rgba(239,68,68,.3); padding: 10px 14px; border-radius: 8px; font-size: 0.83rem; }

.rp-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; flex-wrap: wrap; padding: 16px 18px; background: #0f1419; border: 1px solid #2a3041; border-left: 4px solid #a78bfa; border-radius: 10px; }
.rp-head-l { min-width: 0; flex: 1 1 260px; }
.rp-head-r { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.cap { font-size: 0.8rem; color: #e2e8f0; font-weight: 700; overflow-wrap: anywhere; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip { font-size: 0.7rem; padding: 2px 8px; border-radius: 999px; background: #151c27; border: 1px solid #2a3041; color: #c9d4e0; white-space: nowrap; }
.chip.dim { color: #8892a4; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }

.card { background: #0f1419; border: 1px solid #2a3041; border-radius: 10px; padding: 16px 18px; min-width: 0; }
.card-title { font-size: 0.86rem; font-weight: 700; color: #e2e8f0; margin-bottom: 12px; display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.src { font-size: 0.7rem; color: #8892a4; font-weight: 500; }
.muted { color: #8892a4; }
.small { font-size: 0.76rem; line-height: 1.6; }
.grid2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }

/* 감성 게이지 (초록 = 긍정, 빨강 = 부정, 회색 = 중립 — 주가 방향 색과 무관) */
.gauges { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.gauge { background: #0b0f14; border: 1px solid #1e2533; border-radius: 8px; padding: 10px 12px; min-width: 0; }
.g-top { display: flex; justify-content: space-between; align-items: baseline; gap: 6px; }
.g-label { font-size: 0.74rem; color: #8892a4; }
.g-top b { font-family: 'JetBrains Mono', monospace; font-size: 1.05rem; }
.g-track { position: relative; height: 10px; background: #1e2533; border-radius: 999px; margin: 8px 0 6px; overflow: hidden; }
.g-mid { position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: #8892a4; opacity: .6; }
.g-fill { position: absolute; top: 0; bottom: 0; border-radius: 999px; background: #8892a4; transition: width .5s ease, left .5s ease; }
.g-fill.pos { background: #10b981; } .g-fill.neg { background: #ef4444; } .g-fill.neu { background: #8892a4; }
.g-sub { font-size: 0.7rem; color: #8892a4; overflow-wrap: anywhere; }
b.pos, .pos { color: #10b981; } b.neg, .neg { color: #ef4444; } b.neu { color: #8892a4; }

.summary { margin: 0; color: #c9d4e0; font-size: 0.86rem; line-height: 1.85; overflow-wrap: anywhere; }
.ref { display: inline-block; margin: 0 2px; padding: 0 6px; border-radius: 4px; border: 1px solid #2a3041; background: #151c27; color: #93c5fd; font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; line-height: 1.6; cursor: pointer; vertical-align: 1px; }
.ref:hover { border-color: #3b82f6; color: #bfdbfe; }
.lst { margin: 0; padding-left: 18px; color: #c9d4e0; font-size: 0.84rem; line-height: 1.75; overflow-wrap: anywhere; }
.lst.risk li::marker { color: #ef4444; }

.grp + .grp { margin-top: 14px; }
.grp-title { font-size: 0.78rem; font-weight: 700; color: #a78bfa; margin-bottom: 6px; }
.grp-title .cnt { color: #8892a4; font-weight: 500; margin-left: 4px; }
.finding { padding: 9px 10px; border: 1px solid transparent; border-bottom-color: #1e2533; border-radius: 6px; transition: background .3s, border-color .3s; min-width: 0; }
.finding.flash { background: rgba(59,130,246,.12); border-color: #3b82f6; }
.f-top { display: flex; gap: 8px; align-items: flex-start; }
.rid { flex-shrink: 0; font-family: 'JetBrains Mono', monospace; font-size: 0.68rem; background: #1e2533; color: #93c5fd; padding: 2px 6px; border-radius: 4px; margin-top: 2px; }
.claim { color: #e2e8f0; font-size: 0.85rem; line-height: 1.6; overflow-wrap: anywhere; min-width: 0; }
.detail { color: #8892a4; font-size: 0.76rem; line-height: 1.6; margin: 4px 0 0 34px; overflow-wrap: anywhere; }
.f-meta { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; margin: 6px 0 0 34px; font-size: 0.7rem; color: #8892a4; }
.f-meta .pub { color: #a0aec0; }
.topic { padding: 1px 6px; border-radius: 4px; background: #151c27; color: #c9d4e0; }
.imp { color: #f59e0b; }
.schip { flex-shrink: 0; font-size: 0.68rem; padding: 1px 7px; border-radius: 999px; border: 1px solid #2a3041; color: #8892a4; white-space: nowrap; }
.schip.pos { color: #10b981; border-color: rgba(16,185,129,.45); background: rgba(16,185,129,.08); }
.schip.neg { color: #ef4444; border-color: rgba(239,68,68,.45); background: rgba(239,68,68,.08); }
.src-link { color: #3b82f6; text-decoration: none; white-space: nowrap; }
.src-link:hover { text-decoration: underline; }
.more { margin-top: 8px; background: transparent; border: 1px dashed #2a3041; color: #c9d4e0; border-radius: 6px; padding: 5px 12px; font-size: 0.74rem; cursor: pointer; font-family: inherit; }

.plan { margin-bottom: 10px; color: #c9d4e0; font-size: 0.8rem; }
.plan summary, .qs summary { cursor: pointer; color: #a0aec0; font-size: 0.76rem; }
.plan ol { margin: 6px 0 0; padding-left: 20px; line-height: 1.7; }
.timeline { list-style: none; margin: 0; padding: 0; }
.tl-item { display: flex; gap: 10px; padding-bottom: 12px; position: relative; }
.tl-item:not(:last-child)::before { content: ''; position: absolute; left: 5px; top: 14px; bottom: 0; width: 1px; background: #2a3041; }
.tl-dot { width: 11px; height: 11px; border-radius: 50%; margin-top: 4px; flex-shrink: 0; background: #3b82f6; }
.tl-dot.refl { background: #a78bfa; }
.tl-body { min-width: 0; flex: 1; }
.tl-head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 0.8rem; color: #e2e8f0; }
.gaps { display: flex; flex-wrap: wrap; gap: 5px; align-items: center; margin-top: 6px; }
.gap-cap { font-size: 0.7rem; color: #f59e0b; font-weight: 700; }
.gap { font-size: 0.7rem; padding: 1px 7px; border-radius: 4px; background: rgba(245,158,11,.1); color: #fcd34d; overflow-wrap: anywhere; }
.qs { margin-top: 6px; }
.q-list { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.q { font-size: 0.7rem; padding: 1px 7px; border-radius: 4px; background: #151c27; border: 1px solid #1e2533; color: #c9d4e0; overflow-wrap: anywhere; max-width: 100%; }
.errs { margin-top: 6px; font-size: 0.68rem; color: #fca5a5; line-height: 1.6; overflow-wrap: anywhere; }
.remain { margin-top: 6px; padding: 10px 12px; border: 1px dashed rgba(245,158,11,.4); border-radius: 8px; }
.remain ul { margin: 6px 0 0; padding-left: 18px; color: #c9d4e0; font-size: 0.8rem; line-height: 1.7; overflow-wrap: anywhere; }

.com-disc { font-size: 0.74rem; color: #f59e0b; background: rgba(245,158,11,.07); border: 1px solid rgba(245,158,11,.25); padding: 7px 10px; border-radius: 6px; margin-bottom: 10px; }
.post { display: flex; gap: 8px; align-items: flex-start; padding: 7px 0; border-bottom: 1px dashed #1e2533; min-width: 0; }
.p-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.p-title { color: #e2e8f0; font-size: 0.82rem; text-decoration: none; overflow-wrap: anywhere; }
a.p-title:hover { color: #93c5fd; }
.p-meta { font-size: 0.68rem; color: #8892a4; }
.notes { margin: 10px 0 0; padding-left: 18px; font-size: 0.72rem; color: #8892a4; line-height: 1.7; overflow-wrap: anywhere; }

.btn { border-radius: 8px; padding: 9px 16px; font-size: 0.84rem; font-weight: 600; cursor: pointer; font-family: inherit; border: 1px solid transparent; }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.btn.primary { background: #3b82f6; color: #fff; }
.btn.sm { padding: 6px 12px; font-size: 0.78rem; }

@media (max-width: 860px) {
  .grid2 { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 560px) {
  .gauges { grid-template-columns: minmax(0, 1fr); }
  .card { padding: 14px 12px; }
  .rp-head { padding: 14px 12px; }
  .detail, .f-meta { margin-left: 0; }
}
</style>
