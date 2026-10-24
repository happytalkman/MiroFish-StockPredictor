<template>
  <section class="cd" :class="{ closed: !open }">
    <!-- 머리 -->
    <header class="cd-head">
      <div class="cd-title">
        <span class="cd-ico">⚖</span>
        <div>
          <div class="t">투자위원회 토론</div>
          <div class="m">
            <span>{{ roundsHeld }}라운드</span>
            <span>· 정정 {{ correctionsCount }}건</span>
            <span v-if="facts.length">· 사실 {{ facts.length }}개</span>
            <span v-if="c.created_at">· {{ String(c.created_at).replace('T', ' ') }}</span>
          </div>
        </div>
      </div>
      <div class="cd-actions">
        <select v-model.number="rerunRounds" class="cd-sel" :disabled="busy" aria-label="토론 라운드 수">
          <option :value="1">1라운드</option>
          <option :value="2">2라운드</option>
          <option :value="3">3라운드</option>
        </select>
        <button type="button" class="cd-btn" :disabled="busy" @click="$emit('rerun', rerunRounds)">↻ 토론 다시 하기</button>
        <button type="button" class="cd-btn ghost" :aria-expanded="open" @click="open = !open">{{ open ? '접기 ▴' : '펼치기 ▾' }}</button>
      </div>
    </header>

    <!-- 점수 막대 (접혀 있어도 보임) -->
    <div class="score">
      <div class="sb-lab">
        <span class="bull">강세 <b>{{ fmtScore(fin.bull_score) }}</b></span>
        <span class="cons" :class="'c-' + (fin.consensus || 'split')">{{ fin.consensus_label || consensusLabel(fin.consensus) }}</span>
        <span class="bear"><b>{{ fmtScore(fin.bear_score) }}</b> 약세</span>
      </div>
      <div class="sbar" role="img" :aria-label="`강세 ${fmtScore(fin.bull_score)}점, 약세 ${fmtScore(fin.bear_score)}점`">
        <div class="sb-bull" :style="{ width: bullW + '%' }"></div>
        <div class="sb-bear" :style="{ width: (100 - bullW) + '%' }"></div>
      </div>
      <div class="sb-meta">
        <span v-if="fin.recommended_label">의장 권고 <b>{{ fin.recommended_label }}</b></span>
        <span v-if="fin.position_cap_pct != null">· 비중 상한 {{ fin.position_cap_pct }}%</span>
        <span v-if="c.jev_bull_pct != null" class="jev">· JEV 강세 논거 설득력 <b :class="pctCls(c.jev_bull_pct)">{{ c.jev_bull_pct }}%</b></span>
        <span v-if="fin.fallback" class="warn">· 의장 요약 실패 — 규칙 집계</span>
      </div>
    </div>

    <div v-show="open" class="cd-body">
      <p v-if="c.stale_note" class="stale">{{ c.stale_note }}</p>

      <!-- 최종 요약 -->
      <div class="final">
        <div class="final-h"><span class="av mod">의</span><b>의장 최종 요약</b></div>
        <p class="final-t">
          <template v-for="(sg, i) in segs(fin.summary)" :key="'fs' + i">
            <template v-if="sg.t === 'txt'">{{ sg.v }}</template>
            <template v-else><button v-for="id in sg.ids" :key="id" type="button" class="fchip" :class="chipCls(id)" @click.stop="openFact($event, id)" @mouseenter="hoverFact($event, id)" @mouseleave="leaveFact">{{ id }}</button></template>
          </template>
          <span v-if="!fin.summary" class="muted">요약이 없습니다.</span>
        </p>

        <div v-if="keyIssues.length" class="issues">
          <div class="sub-h">핵심 쟁점 <small>의장 판단 · JEV 가 매긴 '강세 측 견해가 옳다' 확률</small></div>
          <div v-for="k in keyIssues" :key="k.id" class="issue">
            <div class="issue-top">
              <span class="iid">{{ k.id }}</span>
              <span class="itext">{{ k.issue }}</span>
              <span class="lean" :class="'l-' + (k.leaning || 'neutral')">{{ leanLabel(k.leaning) }}</span>
              <span v-if="k.jev_bull_pct != null" class="jevp" :class="pctCls(k.jev_bull_pct)" title="JEV: 강세 측 견해가 옳을 확률">JEV {{ k.jev_bull_pct }}%</span>
            </div>
            <div v-if="k.bull_view || k.bear_view" class="views">
              <span v-if="k.bull_view" class="v-bull">강세: {{ k.bull_view }}</span>
              <span v-if="k.bear_view" class="v-bear">약세: {{ k.bear_view }}</span>
            </div>
            <div v-if="k.why" class="why">
              <template v-for="(sg, i) in segs(k.why)" :key="k.id + 'w' + i">
                <template v-if="sg.t === 'txt'">{{ sg.v }}</template>
                <template v-else><button v-for="id in sg.ids" :key="id" type="button" class="fchip" :class="chipCls(id)" @click.stop="openFact($event, id)" @mouseenter="hoverFact($event, id)" @mouseleave="leaveFact">{{ id }}</button></template>
              </template>
            </div>
            <span v-if="k.status === 'resolved'" class="resolved">해결됨</span>
          </div>
        </div>
        <div v-if="(fin.unresolved || []).length" class="unres">
          <div class="sub-h">미해결</div>
          <ul><li v-for="(u, i) in fin.unresolved" :key="i">{{ u }}</li></ul>
        </div>
      </div>

      <!-- 라운드 탭 -->
      <div v-if="tabs.length > 1" class="tabs" role="tablist">
        <button v-for="t in tabs" :key="t.key" type="button" role="tab" class="tab" :class="{ on: tab === t.key }"
                :aria-selected="tab === t.key" @click="tab = t.key">{{ t.label }}</button>
      </div>

      <!-- 발언 기록 -->
      <div v-if="tab !== 'facts'" class="speeches">
        <article v-for="(e, ei) in entries" :key="tab + '-' + ei" class="sp" :class="'sp-' + spk(e.speaker).cls">
          <header class="sp-h">
            <span class="av" :class="spk(e.speaker).cls">{{ spk(e.speaker).short }}</span>
            <div class="sp-t"><b>{{ e.speaker_label || spk(e.speaker).label }}</b><span class="kind">{{ e.kind_label }}</span></div>
            <span v-if="confOf(e) != null" class="conf">확신 {{ confOf(e) }}%</span>
          </header>

          <div v-if="e.failed" class="sp-fail">발언을 받지 못했습니다 — {{ e.error }}</div>

          <!-- 모두발언 -->
          <template v-else-if="e.kind === 'opening'">
            <p v-if="e.content.thesis" class="thesis">
              <template v-for="(sg, i) in segs(e.content.thesis)" :key="'th' + i">
                <template v-if="sg.t === 'txt'">{{ sg.v }}</template>
                <template v-else><button v-for="id in sg.ids" :key="id" type="button" class="fchip" :class="chipCls(id)" @click.stop="openFact($event, id)" @mouseenter="hoverFact($event, id)" @mouseleave="leaveFact">{{ id }}</button></template>
              </template>
            </p>
            <ol class="args">
              <li v-for="(a, ai) in e.content.arguments || []" :key="ai" :class="{ flagged: a.flag }">
                <div class="claim">{{ a.claim }}</div>
                <div class="arg-meta">
                  <button v-for="id in a.facts || []" :key="id" type="button" class="fchip" :class="chipCls(id)" @click.stop="openFact($event, id)" @mouseenter="hoverFact($event, id)" @mouseleave="leaveFact">{{ id }}</button>
                  <span class="dots" :title="'논거 강도 ' + (a.strength || 3) + '/5'"><i v-for="n in 5" :key="n" :class="{ on: n <= (a.strength || 3) }"></i></span>
                  <span v-if="a.flag === 'no_facts'" class="flag">사실 ID 없음</span>
                  <span v-else-if="(a.invalid_facts || []).length" class="flag">무효 ID {{ a.invalid_facts.join(', ') }}</span>
                  <span v-if="(a.unverified_numbers || []).length" class="flag soft" title="사실표에서 같은 값을 찾지 못한 수치">미확인 수치 {{ a.unverified_numbers.join(', ') }}</span>
                </div>
              </li>
            </ol>
            <div v-if="e.content.price_view && (e.content.price_view.target || e.content.price_view.horizon)" class="pv">
              <span v-if="e.content.price_view.target">목표가 <b>{{ won(e.content.price_view.target) }}</b></span>
              <span v-if="e.content.price_view.horizon">기간 {{ e.content.price_view.horizon }}</span>
            </div>
          </template>

          <!-- 리스크 검토 -->
          <template v-else-if="e.kind === 'review'">
            <ul class="risks">
              <li v-for="(r, ri) in e.content.key_risks || []" :key="ri">
                <span class="sev" :title="'심각도 ' + (r.severity || 3) + '/5'"><i v-for="n in 5" :key="n" :class="{ on: n <= (r.severity || 3) }"></i></span>
                <span class="rtext">{{ r.risk }}</span>
                <button v-for="id in r.facts || []" :key="id" type="button" class="fchip" :class="chipCls(id)" @click.stop="openFact($event, id)" @mouseenter="hoverFact($event, id)" @mouseleave="leaveFact">{{ id }}</button>
                <span v-if="r.flag === 'no_facts'" class="flag">사실 ID 없음</span>
              </li>
            </ul>
            <div class="rk-grid">
              <div v-if="e.content.position_limit_pct != null"><span>비중 한도</span><b>계좌의 {{ e.content.position_limit_pct }}%</b></div>
              <div v-if="e.content.stop_rule"><span>손절 규칙</span><b>{{ e.content.stop_rule }}</b></div>
            </div>
            <div v-if="e.content.questions && (e.content.questions.bull || e.content.questions.bear)" class="qs">
              <div v-if="e.content.questions.bull" class="q q-bull"><span>강세 측에 질문</span>{{ e.content.questions.bull }}</div>
              <div v-if="e.content.questions.bear" class="q q-bear"><span>약세 측에 질문</span>{{ e.content.questions.bear }}</div>
            </div>
          </template>

          <!-- 의장 사실 검증 -->
          <template v-else-if="e.kind === 'factcheck'">
            <div v-if="!(e.content.corrections || []).length" class="ok-line">✓ 정정할 사실 오류가 없습니다</div>
            <div v-for="(x, xi) in e.content.corrections || []" :key="xi" class="corr" :class="x.source">
              <div class="corr-h">
                <span class="corr-tag">정정</span>
                <span class="who" :class="'w-' + (x.who || 'all')">{{ whoLabel(x.who) }}</span>
                <span v-if="x.source === 'auto'" class="auto" title="코드가 사실 ID 인용을 자동 점검한 결과">자동 점검</span>
              </div>
              <div v-if="x.claim" class="corr-claim">「{{ x.claim }}」</div>
              <div v-if="x.problem" class="corr-prob">문제: {{ x.problem }}</div>
              <div v-if="x.correction" class="corr-fix">→ {{ x.correction }}
                <button v-for="id in x.correct_fact_ids || []" :key="id" type="button" class="fchip" :class="chipCls(id)" @click.stop="openFact($event, id)" @mouseenter="hoverFact($event, id)" @mouseleave="leaveFact">{{ id }}</button>
              </div>
            </div>
            <div v-if="(e.content.issues || []).length" class="mini-issues">
              <div v-for="i in e.content.issues" :key="i.id" class="mi">
                <span class="iid">{{ i.id }}</span>
                <span class="mi-t">{{ i.issue }}</span>
                <span class="st" :class="i.status">{{ i.status === 'resolved' ? '해결' : '미해결' }}</span>
              </div>
            </div>
            <div v-if="e.content.focus_next" class="focus">다음 초점 · {{ e.content.focus_next }}</div>
          </template>

          <!-- 반박 -->
          <template v-else-if="e.kind === 'rebuttal'">
            <div v-for="(r, ri) in e.content.rebuttals || []" :key="ri" class="reb">
              <div v-if="r.target_claim" class="reb-tgt">↳ 「{{ r.target_claim }}」</div>
              <div class="reb-r">{{ r.response }}
                <button v-for="id in r.facts || []" :key="id" type="button" class="fchip" :class="chipCls(id)" @click.stop="openFact($event, id)" @mouseenter="hoverFact($event, id)" @mouseleave="leaveFact">{{ id }}</button>
                <span v-if="r.flag === 'no_facts'" class="flag">사실 ID 없음</span>
                <span v-else-if="(r.invalid_facts || []).length" class="flag">무효 ID {{ r.invalid_facts.join(', ') }}</span>
              </div>
            </div>
            <div v-if="(e.content.concessions || []).length" class="conc">
              <span class="conc-h">인정</span>
              <ul><li v-for="(x, xi) in e.content.concessions" :key="xi">{{ x }}</li></ul>
            </div>
            <div v-if="e.content.answer_to_risk" class="ans"><span>리스크 매니저 질문에 대한 답</span>{{ e.content.answer_to_risk }}</div>
          </template>

          <p v-else class="plain">{{ e.text }}</p>

          <div v-if="e.error && !e.failed" class="sp-warn">응답 일부가 불완전했습니다 — {{ e.error }}</div>
        </article>
        <p v-if="!entries.length" class="muted small">이 라운드의 발언 기록이 없습니다.</p>
      </div>

      <!-- 사실표 -->
      <div v-else class="facts">
        <p class="muted small">토론에서 근거로 인정되는 사실은 아래 번호가 붙은 항목뿐입니다. 사실표에 없는 수치·뉴스는 의장이 정정합니다.</p>
        <div v-for="f in facts" :key="f.id" class="fact">
          <span class="fchip static" :class="chipCls(f.id)">{{ f.id }}</span>
          <div class="fact-b">
            <div class="fact-t">{{ f.text }}</div>
            <div v-if="f.publisher || f.date || safeUrl(f.url)" class="fact-m">
              <span v-if="f.publisher">{{ f.publisher }}</span>
              <span v-if="f.date">{{ f.date }}</span>
              <a v-if="safeUrl(f.url)" :href="safeUrl(f.url)" target="_blank" rel="noopener noreferrer">출처 ↗</a>
            </div>
          </div>
        </div>
      </div>

      <p class="note small">토론 참가자는 모두 AI 입니다. 모든 주장은 번호 사실표만 근거로 인정되며, 의장 점수·결론은 JEV 채점에 전달하지 않습니다(앵커링 방지).</p>
    </div>

    <!-- 사실 팝오버 (호버·탭) -->
    <Teleport to="body">
      <div v-if="pop.id" class="cd-pop" :class="{ above: pop.above }" :style="popStyle"
           role="dialog" :aria-label="'사실 ' + pop.id" @mouseenter="cancelHide" @mouseleave="leaveFact" @click.stop>
        <div class="pop-h">
          <span class="fchip static" :class="chipCls(pop.id)">{{ pop.id }}</span>
          <span class="pop-k">{{ popFact ? kindLabel(popFact) : '무효 인용' }}</span>
          <button type="button" class="pop-x" aria-label="닫기" @click="closePop">✕</button>
        </div>
        <div class="pop-t">{{ popFact ? popFact.text : '사실표에 없는 ID 입니다 — 근거로 인정되지 않습니다.' }}</div>
        <div v-if="popFact && (popFact.publisher || popFact.date)" class="pop-m">{{ popFact.publisher || '' }} {{ popFact.date || '' }}</div>
        <a v-if="popFact && safeUrl(popFact.url)" class="pop-a" :href="safeUrl(popFact.url)" target="_blank" rel="noopener noreferrer">출처 열기 ↗</a>
      </div>
    </Teleport>
  </section>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  reportId: { type: String, required: true },
  committee: { type: Object, required: true },
  busy: { type: Boolean, default: false }
})
defineEmits(['rerun'])

const SPK = {
  bull: { label: '강세 애널리스트', short: '강', cls: 'bull' },
  bear: { label: '약세 애널리스트', short: '약', cls: 'bear' },
  risk: { label: '리스크 매니저', short: '리', cls: 'risk' },
  moderator: { label: '의장', short: '의', cls: 'mod' }
}
const WHO = { bull: '강세', bear: '약세', risk: '리스크', moderator: '의장' }
const KIND = { price: '가격·지표', fund: '재무', vote: '에이전트 투표', research: '리서치', community: '커뮤니티', kg: '지식그래프' }
const CONS = { bull: '강세 우위', bear: '약세 우위', split: '의견 양분' }

const open = ref(true)
const rerunRounds = ref(2)
const tab = ref('r1')

const c = computed(() => props.committee || {})
const fin = computed(() => c.value.final || {})
const facts = computed(() => (Array.isArray(c.value.facts) ? c.value.facts : []))
const factMap = computed(() => Object.fromEntries(facts.value.map(f => [f.id, f])))
const transcript = computed(() => (Array.isArray(c.value.transcript) ? c.value.transcript : []))
const roundsHeld = computed(() => c.value.rounds_held || Math.max(1, ...transcript.value.map(t => t.round || 1)))
const correctionsCount = computed(() => (Array.isArray(c.value.corrections) ? c.value.corrections.length : (fin.value.fact_corrections_count || 0)))
const keyIssues = computed(() => (Array.isArray(fin.value.key_issues) ? fin.value.key_issues : []))

const tabs = computed(() => {
  const rounds = [...new Set(transcript.value.filter(t => t.kind !== 'summary').map(t => t.round))].sort((a, b) => a - b)
  const out = rounds.map(r => ({ key: 'r' + r, label: `${r}라운드 · ${r === 1 ? '모두발언' : '반박'}` }))
  if (facts.value.length) out.push({ key: 'facts', label: `사실표 ${facts.value.length}` })
  return out
})
watch(tabs, (t) => { if (t.length && !t.some(x => x.key === tab.value)) tab.value = t[0].key }, { immediate: true })
const entries = computed(() => {
  const r = Number(String(tab.value).replace('r', ''))
  return transcript.value.filter(t => t.round === r && t.kind !== 'summary' && t.content)
})

const bullW = computed(() => {
  const b = Number(fin.value.bull_score) || 0
  const s = Number(fin.value.bear_score) || 0
  return b + s > 0 ? Math.round((b / (b + s)) * 1000) / 10 : 50
})

// ── 본문 속 [F1, R2] → 칩
const ID_GROUP = /\[\s*([FRCKHfrckh]\s*\d[\dFRCKHfrckh\s,·/-]{0,60})\]/g
function segs(text) {
  const s = String(text || '')
  const out = []
  let last = 0
  for (const m of s.matchAll(ID_GROUP)) {
    const ids = [...m[1].toUpperCase().matchAll(/([FRCKH])\s*-?\s*(\d{1,3})/g)].map(x => x[1] + Number(x[2]))
    if (!ids.length) continue
    if (m.index > last) out.push({ t: 'txt', v: s.slice(last, m.index) })
    out.push({ t: 'ids', ids: [...new Set(ids)] })
    last = m.index + m[0].length
  }
  if (last < s.length) out.push({ t: 'txt', v: s.slice(last) })
  return out
}

const spk = (k) => SPK[k] || { label: k || '참가자', short: '?', cls: 'mod' }
const whoLabel = (w) => WHO[w] || '공통'
const leanLabel = (l) => ({ bull: '강세 쪽', bear: '약세 쪽' }[l] || '중립')
const consensusLabel = (x) => CONS[x] || '의견 양분'
const kindLabel = (f) => KIND[f.kind] || '사실'
const fmtScore = (v) => (v == null || v === '' ? '-' : Math.round(Number(v)))
const pctCls = (p) => (p == null ? '' : p >= 60 ? 'p-bull' : p <= 40 ? 'p-bear' : 'p-mid')
const won = (v) => (v == null ? '-' : `${Math.round(Number(v)).toLocaleString('ko-KR')}원`)
const safeUrl = (u) => (typeof u === 'string' && /^https?:\/\//i.test(u.trim()) ? u.trim() : null)
function chipCls(id) {
  const k = String(id || '').charAt(0)
  return [factMap.value[id] ? 'k-' + k : 'invalid', pop.id === id ? 'active' : '']
}
function confOf(e) {
  const v = e.kind === 'opening' ? e.content?.confidence : e.kind === 'rebuttal' ? e.content?.updated_confidence : null
  return v == null ? null : Math.round(Number(v))
}

// ── 사실 팝오버: 데스크톱은 호버, 모바일은 탭(고정)
const pop = reactive({ id: '', x: 8, top: 0, bottom: 0, above: false, pinned: false })
const popFact = computed(() => factMap.value[pop.id] || null)
const popStyle = computed(() => (pop.above ? { left: pop.x + 'px', bottom: pop.bottom + 'px' } : { left: pop.x + 'px', top: pop.top + 'px' }))
let hideTimer = null
const canHover = () => typeof window !== 'undefined' && window.matchMedia && window.matchMedia('(hover: hover)').matches

function place(el, id, pinned) {
  if (!el || typeof window === 'undefined') return
  const r = el.getBoundingClientRect()
  const vw = window.innerWidth
  const vh = window.innerHeight
  const W = Math.min(320, vw - 16)
  pop.x = Math.max(8, Math.min(r.left, vw - W - 8))
  pop.above = r.bottom + 170 > vh && r.top > 180
  pop.top = r.bottom + 6
  pop.bottom = vh - r.top + 6
  pop.id = id
  pop.pinned = pinned
}
function openFact(ev, id) {
  clearTimeout(hideTimer)
  if (pop.id === id && pop.pinned) { closePop(); return }
  place(ev.currentTarget, id, true)
}
function hoverFact(ev, id) {
  if (!canHover() || pop.pinned) return
  clearTimeout(hideTimer)
  place(ev.currentTarget, id, false)
}
function leaveFact() {
  if (pop.pinned) return
  clearTimeout(hideTimer)
  hideTimer = setTimeout(closePop, 220)
}
function cancelHide() { clearTimeout(hideTimer) }
function closePop() { clearTimeout(hideTimer); pop.id = ''; pop.pinned = false }
const onDoc = () => { if (pop.id) closePop() }
const onKey = (e) => { if (e.key === 'Escape' && pop.id) closePop() }

onMounted(() => {
  document.addEventListener('click', onDoc)
  document.addEventListener('keydown', onKey)
  window.addEventListener('scroll', onDoc, true)
  window.addEventListener('resize', onDoc)
})
onBeforeUnmount(() => {
  clearTimeout(hideTimer)
  document.removeEventListener('click', onDoc)
  document.removeEventListener('keydown', onKey)
  window.removeEventListener('scroll', onDoc, true)
  window.removeEventListener('resize', onDoc)
})
watch(() => props.committee, () => closePop())
</script>

<style scoped>
.cd { background: #0f1419; border: 1px solid #2a3041; border-radius: 12px; padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; min-width: 0; font-family: 'Noto Sans KR', 'Inter', sans-serif; }
.cd-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; flex-wrap: wrap; }
.cd-title { display: flex; gap: 10px; align-items: center; min-width: 0; }
.cd-ico { width: 34px; height: 34px; border-radius: 9px; display: grid; place-items: center; background: rgba(167,139,250,.12); color: #a78bfa; font-size: 1.05rem; flex-shrink: 0; }
.cd-title .t { color: #e2e8f0; font-weight: 800; font-size: 0.98rem; }
.cd-title .m { color: #8892a4; font-size: 0.72rem; display: flex; flex-wrap: wrap; gap: 4px; margin-top: 2px; }
.cd-actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.cd-sel { background: #0b0f14; color: #c9d4e0; border: 1px solid #2a3041; border-radius: 7px; padding: 6px 8px; font-size: 0.76rem; font-family: inherit; }
.cd-btn { background: #1e2533; color: #e2e8f0; border: 1px solid #2a3041; border-radius: 7px; padding: 6px 11px; font-size: 0.76rem; font-weight: 600; cursor: pointer; font-family: inherit; }
.cd-btn:hover:not(:disabled) { border-color: #a78bfa; }
.cd-btn:disabled, .cd-sel:disabled { opacity: .45; cursor: not-allowed; }
.cd-btn.ghost { background: transparent; color: #a0aec0; }

/* 점수 막대 */
.score { display: flex; flex-direction: column; gap: 6px; }
.sb-lab { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 0.8rem; }
.sb-lab .bull { color: #fca5a5; } .sb-lab .bear { color: #93c5fd; }
.sb-lab b { font-family: 'JetBrains Mono', monospace; font-size: 1.05rem; }
.sb-lab .bull b { color: #ef4444; } .sb-lab .bear b { color: #3b82f6; }
.cons { font-size: 0.72rem; padding: 2px 10px; border-radius: 999px; border: 1px solid #2a3041; color: #c9d4e0; white-space: nowrap; }
.cons.c-bull { border-color: rgba(239,68,68,.5); color: #fca5a5; background: rgba(239,68,68,.08); }
.cons.c-bear { border-color: rgba(59,130,246,.5); color: #93c5fd; background: rgba(59,130,246,.08); }
.sbar { display: flex; height: 10px; border-radius: 999px; overflow: hidden; background: #1e2533; }
.sb-bull { background: linear-gradient(90deg, #b91c1c, #ef4444); transition: width .6s ease; }
.sb-bear { background: linear-gradient(90deg, #3b82f6, #1d4ed8); transition: width .6s ease; }
.sb-meta { display: flex; flex-wrap: wrap; gap: 4px 6px; font-size: 0.74rem; color: #8892a4; }
.sb-meta b { color: #e2e8f0; }
.sb-meta .warn { color: #fbbf24; }

.cd-body { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.stale { margin: 0; font-size: 0.74rem; color: #fbbf24; background: rgba(245,158,11,.08); border: 1px solid rgba(245,158,11,.3); border-radius: 7px; padding: 7px 10px; }

/* 최종 요약 */
.final { background: #121821; border: 1px solid #2a3041; border-left: 3px solid #a78bfa; border-radius: 10px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
.final-h { display: flex; align-items: center; gap: 8px; color: #e2e8f0; font-size: 0.84rem; }
.final-t { margin: 0; color: #c9d4e0; font-size: 0.84rem; line-height: 1.75; white-space: pre-wrap; word-break: keep-all; overflow-wrap: anywhere; }
.sub-h { font-size: 0.76rem; font-weight: 700; color: #e2e8f0; margin-bottom: 6px; display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; }
.sub-h small { color: #8892a4; font-weight: 400; font-size: 0.68rem; }
.issues { display: flex; flex-direction: column; gap: 6px; }
.issue { background: #0b0f14; border: 1px solid #1e2533; border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 5px; }
.issue-top { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.iid { font-family: 'JetBrains Mono', monospace; font-size: 0.66rem; color: #c4b5fd; background: rgba(167,139,250,.12); padding: 1px 6px; border-radius: 4px; flex-shrink: 0; }
.itext { color: #e2e8f0; font-size: 0.8rem; flex: 1 1 160px; min-width: 0; overflow-wrap: anywhere; }
.lean { font-size: 0.68rem; padding: 1px 8px; border-radius: 999px; border: 1px solid #2a3041; color: #a0aec0; white-space: nowrap; }
.lean.l-bull { color: #fca5a5; border-color: rgba(239,68,68,.45); }
.lean.l-bear { color: #93c5fd; border-color: rgba(59,130,246,.45); }
.jevp { font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; padding: 1px 7px; border-radius: 999px; background: #1e2533; color: #e2e8f0; white-space: nowrap; }
.p-bull { color: #ef4444 !important; } .p-bear { color: #3b82f6 !important; } .p-mid { color: #f59e0b !important; }
.jevp.p-bull { background: rgba(239,68,68,.12); } .jevp.p-bear { background: rgba(59,130,246,.12); } .jevp.p-mid { background: rgba(245,158,11,.12); }
.views { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 0.72rem; }
.v-bull { color: #fca5a5; } .v-bear { color: #93c5fd; }
.why { font-size: 0.74rem; color: #a0aec0; line-height: 1.6; overflow-wrap: anywhere; }
.resolved { align-self: flex-start; font-size: 0.64rem; color: #6ee7b7; border: 1px solid rgba(16,185,129,.4); border-radius: 4px; padding: 0 6px; }
.unres ul { margin: 0; padding-left: 18px; color: #c9d4e0; font-size: 0.78rem; line-height: 1.7; }

/* 탭 */
.tabs { display: flex; flex-wrap: wrap; gap: 6px; border-bottom: 1px solid #1e2533; padding-bottom: 8px; }
.tab { background: transparent; border: 1px solid #2a3041; color: #a0aec0; border-radius: 999px; padding: 5px 12px; font-size: 0.76rem; cursor: pointer; font-family: inherit; }
.tab.on { background: rgba(167,139,250,.14); border-color: #a78bfa; color: #e2e8f0; font-weight: 700; }

/* 발언 카드 */
.speeches { display: flex; flex-direction: column; gap: 10px; }
.sp { background: #121821; border: 1px solid #2a3041; border-left: 3px solid #a78bfa; border-radius: 10px; padding: 11px 13px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.sp-bull { border-left-color: #ef4444; } .sp-bear { border-left-color: #3b82f6; } .sp-risk { border-left-color: #f59e0b; }
.sp-h { display: flex; align-items: center; gap: 9px; }
.av { width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center; font-size: 0.74rem; font-weight: 800; color: #0b0f14; background: #a78bfa; flex-shrink: 0; }
.av.bull { background: #ef4444; color: #fff; } .av.bear { background: #3b82f6; color: #fff; } .av.risk { background: #f59e0b; } .av.mod { background: #a78bfa; }
.sp-t { display: flex; align-items: baseline; gap: 7px; flex-wrap: wrap; min-width: 0; flex: 1; }
.sp-t b { color: #e2e8f0; font-size: 0.84rem; }
.kind { font-size: 0.7rem; color: #8892a4; }
.conf { font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; color: #c9d4e0; background: #1e2533; border-radius: 999px; padding: 2px 8px; white-space: nowrap; }
.sp-fail { font-size: 0.78rem; color: #fca5a5; background: rgba(239,68,68,.07); border: 1px dashed rgba(239,68,68,.35); border-radius: 7px; padding: 8px 10px; overflow-wrap: anywhere; }
.sp-warn { font-size: 0.7rem; color: #fbbf24; overflow-wrap: anywhere; }
.thesis { margin: 0; color: #e2e8f0; font-size: 0.84rem; font-weight: 600; line-height: 1.65; overflow-wrap: anywhere; }
.args { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 7px; color: #c9d4e0; font-size: 0.81rem; }
.args li::marker { color: #8892a4; font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; }
.args li.flagged .claim { color: #a0aec0; text-decoration: underline dotted rgba(245,158,11,.6); }
.claim { line-height: 1.6; overflow-wrap: anywhere; }
.arg-meta { display: flex; flex-wrap: wrap; gap: 4px 6px; align-items: center; margin-top: 3px; }
.dots, .sev { display: inline-flex; gap: 2px; margin-left: 2px; }
.dots i, .sev i { width: 6px; height: 6px; border-radius: 50%; background: #2a3041; display: inline-block; }
.sp-bull .dots i.on { background: #ef4444; } .sp-bear .dots i.on { background: #3b82f6; }
.sev i.on { background: #f59e0b; }
.flag { font-size: 0.66rem; color: #fca5a5; border: 1px solid rgba(239,68,68,.4); border-radius: 4px; padding: 0 5px; white-space: nowrap; }
.flag.soft { color: #fbbf24; border-color: rgba(245,158,11,.4); }
.pv { display: flex; flex-wrap: wrap; gap: 10px; font-size: 0.76rem; color: #8892a4; }
.pv b { color: #e2e8f0; }
.risks { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
.risks li { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; font-size: 0.8rem; color: #c9d4e0; }
.rtext { flex: 1 1 180px; min-width: 0; overflow-wrap: anywhere; }
.rk-grid { display: grid; grid-template-columns: minmax(0, 160px) minmax(0, 1fr); gap: 8px; }
.rk-grid > div { background: #0b0f14; border: 1px solid #1e2533; border-radius: 7px; padding: 7px 10px; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.rk-grid span { font-size: 0.66rem; color: #8892a4; }
.rk-grid b { font-size: 0.78rem; color: #e2e8f0; font-weight: 600; overflow-wrap: anywhere; }
.qs { display: flex; flex-direction: column; gap: 5px; }
.q { font-size: 0.78rem; color: #c9d4e0; padding: 6px 10px; border-radius: 7px; background: #0b0f14; overflow-wrap: anywhere; }
.q span, .ans span { display: block; font-size: 0.66rem; margin-bottom: 2px; }
.q-bull { border-left: 2px solid #ef4444; } .q-bull span { color: #fca5a5; }
.q-bear { border-left: 2px solid #3b82f6; } .q-bear span { color: #93c5fd; }

/* 정정 */
.ok-line { font-size: 0.78rem; color: #6ee7b7; }
.corr { background: rgba(245,158,11,.06); border: 1px solid rgba(245,158,11,.35); border-radius: 8px; padding: 8px 10px; display: flex; flex-direction: column; gap: 3px; font-size: 0.78rem; color: #c9d4e0; overflow-wrap: anywhere; }
.corr.auto { background: rgba(167,139,250,.05); border-color: rgba(167,139,250,.35); border-style: dashed; }
.corr-h { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.corr-tag { font-size: 0.64rem; font-weight: 800; color: #0b0f14; background: #f59e0b; border-radius: 4px; padding: 0 6px; }
.corr.auto .corr-tag { background: #a78bfa; }
.who { font-size: 0.68rem; color: #a0aec0; }
.who.w-bull { color: #fca5a5; } .who.w-bear { color: #93c5fd; } .who.w-risk { color: #fcd34d; }
.auto { font-size: 0.62rem; color: #c4b5fd; border: 1px solid rgba(167,139,250,.4); border-radius: 4px; padding: 0 5px; }
.corr-claim { color: #a0aec0; }
.corr-prob { color: #fbbf24; }
.corr-fix { color: #e2e8f0; }
.mini-issues { display: flex; flex-direction: column; gap: 4px; }
.mi { display: flex; gap: 6px; align-items: center; font-size: 0.76rem; color: #c9d4e0; }
.mi-t { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.st { font-size: 0.64rem; border-radius: 4px; padding: 0 6px; border: 1px solid #2a3041; color: #fbbf24; white-space: nowrap; }
.st.resolved { color: #6ee7b7; border-color: rgba(16,185,129,.4); }
.focus { font-size: 0.74rem; color: #c4b5fd; overflow-wrap: anywhere; }

/* 반박 */
.reb { display: flex; flex-direction: column; gap: 3px; font-size: 0.8rem; }
.reb-tgt { color: #8892a4; font-size: 0.74rem; overflow-wrap: anywhere; }
.reb-r { color: #c9d4e0; line-height: 1.6; overflow-wrap: anywhere; }
.conc { font-size: 0.76rem; color: #a0aec0; }
.conc-h { font-size: 0.66rem; font-weight: 700; color: #6ee7b7; }
.conc ul { margin: 2px 0 0; padding-left: 18px; line-height: 1.6; }
.ans { font-size: 0.78rem; color: #c9d4e0; background: #0b0f14; border-left: 2px solid #f59e0b; border-radius: 7px; padding: 6px 10px; overflow-wrap: anywhere; }
.ans span { color: #fcd34d; }
.plain { margin: 0; white-space: pre-wrap; color: #c9d4e0; font-size: 0.8rem; overflow-wrap: anywhere; }

/* 사실 칩 */
.fchip { display: inline-flex; align-items: center; font-family: 'JetBrains Mono', monospace; font-size: 0.66rem; line-height: 1.5; padding: 0 6px; margin: 0 2px; border-radius: 4px; border: 1px solid #2a3041; background: #1e2533; color: #e2e8f0; cursor: pointer; vertical-align: 1px; }
.fchip.static { cursor: default; }
.fchip:hover, .fchip.active { border-color: #a78bfa; background: rgba(167,139,250,.18); }
.fchip.k-R { color: #c4b5fd; } .fchip.k-C { color: #fcd34d; } .fchip.k-K { color: #6ee7b7; }
.fchip.invalid { color: #fca5a5; border-style: dashed; border-color: rgba(239,68,68,.55); text-decoration: line-through; }

/* 사실표 */
.facts { display: flex; flex-direction: column; gap: 6px; }
.fact { display: flex; gap: 8px; align-items: flex-start; padding: 6px 0; border-bottom: 1px dashed #1e2533; }
.fact-b { min-width: 0; flex: 1; }
.fact-t { color: #c9d4e0; font-size: 0.8rem; line-height: 1.6; overflow-wrap: anywhere; }
.fact-m { display: flex; flex-wrap: wrap; gap: 8px; font-size: 0.7rem; color: #8892a4; margin-top: 2px; }
.fact-m a { color: #93c5fd; text-decoration: none; }

.muted { color: #8892a4; } .small { font-size: 0.74rem; line-height: 1.6; }
.note { margin: 0; color: #8892a4; }

/* 팝오버 */
.cd-pop { position: fixed; z-index: 9999; width: min(320px, calc(100vw - 16px)); background: #121821; border: 1px solid #a78bfa; border-radius: 10px; padding: 10px 12px; box-shadow: 0 12px 32px rgba(0,0,0,.55); display: flex; flex-direction: column; gap: 6px; font-family: 'Noto Sans KR', 'Inter', sans-serif; }
.pop-h { display: flex; align-items: center; gap: 8px; }
.pop-k { font-size: 0.7rem; color: #8892a4; flex: 1; }
.pop-x { background: transparent; border: 0; color: #8892a4; cursor: pointer; font-size: 0.85rem; padding: 2px 4px; }
.pop-t { color: #e2e8f0; font-size: 0.8rem; line-height: 1.6; overflow-wrap: anywhere; }
.pop-m { font-size: 0.7rem; color: #8892a4; }
.pop-a { font-size: 0.74rem; color: #93c5fd; text-decoration: none; align-self: flex-start; }
.pop-a:hover { text-decoration: underline; }

@media (max-width: 560px) {
  .cd { padding: 13px 12px; }
  .cd-actions { width: 100%; }
  .cd-actions .cd-btn:not(.ghost) { flex: 1; }
  .rk-grid { grid-template-columns: 1fr; }
  .sb-lab { font-size: 0.74rem; }
}
</style>
