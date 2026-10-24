<template>
  <Teleport to="body">
    <Transition name="pm-fade">
      <div v-if="show && !minimized" class="pm-backdrop" role="dialog" aria-modal="true" :aria-label="title">
        <div class="pm-card" :class="'st-' + status">
          <!-- 헤더 -->
          <div class="pm-head">
            <div class="pm-ring" :style="{ '--p': ringPct }">
              <svg viewBox="0 0 120 120" class="pm-ring-svg">
                <circle cx="60" cy="60" r="52" class="track" />
                <circle cx="60" cy="60" r="52" class="bar" :style="{ strokeDashoffset: dashOffset }" />
              </svg>
              <div class="pm-orbit" v-if="status === 'running'"><span></span><span></span><span></span></div>
              <div class="pm-ring-center">
                <template v-if="status === 'done'">
                  <svg viewBox="0 0 52 52" class="pm-check"><path d="M14 27 l8 8 l16 -18" /></svg>
                </template>
                <template v-else-if="status === 'error'"><span class="pm-x">!</span></template>
                <template v-else>
                  <b>{{ Math.round(ringPct) }}<small>%</small></b>
                </template>
              </div>
            </div>
            <div class="pm-titles">
              <div class="pm-title">{{ status === 'done' ? (doneTitle || '완료되었습니다') : status === 'error' ? '작업이 중단되었습니다' : title }}</div>
              <div class="pm-sub">{{ subtitle }}</div>
              <div class="pm-meta">
                <span class="pm-live" v-if="status === 'running'"><i></i>실시간</span>
                <span>경과 {{ elapsedText }}</span>
                <span v-if="etaText && status === 'running'">· {{ etaText }}</span>
              </div>
            </div>
          </div>

          <!-- 현재 작업 -->
          <div class="pm-now" v-if="status !== 'done'">
            <div class="pm-now-label">지금 하는 일</div>
            <Transition name="pm-swap" mode="out-in">
              <div class="pm-now-text" :key="nowKey">
                {{ status === 'error' ? error : (message || '준비 중…') }}<span v-if="status === 'running'" class="pm-dots"><i>.</i><i>.</i><i>.</i></span>
              </div>
            </Transition>
            <div class="pm-bar"><div class="pm-bar-fill" :style="{ width: ringPct + '%' }"></div></div>
          </div>

          <!-- 단계 -->
          <ol class="pm-stages" v-if="stages?.length">
            <li v-for="(s, i) in stages" :key="s.key" :class="'s-' + (s.status || 'pending')">
              <span class="pm-dot">
                <svg v-if="s.status === 'done'" viewBox="0 0 20 20"><path d="M5 10.5 l3 3 l7 -7" /></svg>
                <b v-else-if="s.status === 'error'">!</b>
                <em v-else>{{ i + 1 }}</em>
              </span>
              <span class="pm-stage-label">{{ s.label }}</span>
              <span class="pm-stage-note" v-if="s.note">{{ s.note }}</span>
            </li>
          </ol>

          <!-- 작업 로그 -->
          <div class="pm-log" ref="logEl" v-if="logs?.length">
            <TransitionGroup name="pm-line">
              <div v-for="(l, i) in logs" :key="l.id || i" class="pm-line" :class="l.kind">
                <span class="t">{{ l.t }}</span><span class="x">{{ l.text }}</span>
              </div>
            </TransitionGroup>
          </div>

          <div class="pm-foot">
            <span class="pm-hint" v-if="status === 'running'">창을 닫아도 작업은 계속 진행됩니다.</span>
            <span class="pm-hint" v-else-if="status === 'done'">결과 화면으로 이동합니다…</span>
            <span v-else></span>
            <div class="pm-btns">
              <button v-if="status === 'running' && minimizable" class="pm-btn" @click="minimized = true">최소화</button>
              <button v-if="status === 'error'" class="pm-btn primary" @click="$emit('retry')">다시 시도</button>
              <button v-if="status !== 'running'" class="pm-btn" @click="$emit('close')">닫기</button>
            </div>
          </div>
        </div>
      </div>
    </Transition>

    <!-- 최소화 상태: 우측 하단 진행 칩 -->
    <Transition name="pm-fade">
      <button v-if="show && minimized" class="pm-chip" @click="minimized = false">
        <span class="pm-chip-spin"></span>
        <span class="pm-chip-text">{{ title }} · {{ Math.round(ringPct) }}%</span>
      </button>
    </Transition>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'

const props = defineProps({
  show: { type: Boolean, default: false },
  title: { type: String, default: '작업 중' },
  subtitle: { type: String, default: '' },
  doneTitle: { type: String, default: '' },
  stages: { type: Array, default: () => [] },       // [{key,label,status,note}]
  progress: { type: Number, default: null },         // 0~100
  message: { type: String, default: '' },
  logs: { type: Array, default: () => [] },          // [{id,t,text,kind}]
  status: { type: String, default: 'running' },      // running | done | error
  error: { type: String, default: '' },
  startedAt: { type: Number, default: 0 },
  expectedSec: { type: Number, default: 0 },
  minimizable: { type: Boolean, default: true },
  autoCloseMs: { type: Number, default: 1300 }
})
const emit = defineEmits(['close', 'retry'])

const minimized = ref(false)
const now = ref(Date.now())
let timer = null
const logEl = ref(null)

watch(() => props.show, (v) => {
  if (v) {
    minimized.value = false
    now.value = Date.now()
    clearInterval(timer)
    timer = setInterval(() => (now.value = Date.now()), 500)
  } else {
    clearInterval(timer)
  }
}, { immediate: true })

watch(() => props.status, (st) => {
  if (st === 'done') {
    minimized.value = false
    setTimeout(() => emit('close'), props.autoCloseMs)
  }
})

watch(() => props.logs?.length, async () => {
  await nextTick()
  if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
})

onBeforeUnmount(() => clearInterval(timer))

const stagePct = computed(() => {
  const s = props.stages || []
  if (!s.length) return 0
  const done = s.filter(x => x.status === 'done').length
  const running = s.some(x => x.status === 'running') ? 0.5 : 0
  return ((done + running) / s.length) * 100
})
const ringPct = computed(() => {
  if (props.status === 'done') return 100
  const p = props.progress != null ? props.progress : stagePct.value
  return Math.max(2, Math.min(99, p))
})
const C = 2 * Math.PI * 52
const dashOffset = computed(() => C * (1 - ringPct.value / 100))
const elapsed = computed(() => (props.startedAt ? Math.max(0, (now.value - props.startedAt) / 1000) : 0))
const fmt = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`
const elapsedText = computed(() => fmt(elapsed.value))
const etaText = computed(() => {
  const p = ringPct.value
  if (p >= 8 && elapsed.value > 5) {
    const remain = elapsed.value * (100 - p) / p
    return remain < 20 ? '곧 끝납니다' : `약 ${fmt(remain)} 남음`
  }
  if (props.expectedSec) return `예상 ${fmt(props.expectedSec)}`
  return ''
})
const nowKey = computed(() => (props.message || '').split(' — ')[0])
</script>

<style scoped>
.pm-backdrop {
  position: fixed; inset: 0; z-index: 9999;
  background: radial-gradient(circle at 50% 30%, rgba(37, 99, 235, 0.18), rgba(3, 6, 12, 0.82) 60%);
  backdrop-filter: blur(6px);
  display: flex; align-items: center; justify-content: center; padding: 16px;
}
.pm-card {
  width: min(640px, 100%); max-height: calc(100vh - 32px); overflow: hidden;
  display: flex; flex-direction: column; gap: 16px;
  background: linear-gradient(180deg, #111823, #0c1118);
  border: 1px solid #2a3041; border-radius: 16px; padding: 22px 24px 18px;
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.55), 0 0 0 1px rgba(59, 130, 246, 0.08) inset;
  animation: pm-pop .35s cubic-bezier(.2, .9, .3, 1.2);
}
@keyframes pm-pop { from { transform: translateY(14px) scale(.95); opacity: 0; } to { transform: none; opacity: 1; } }
.pm-card.st-done { border-color: rgba(16, 185, 129, .5); }
.pm-card.st-error { border-color: rgba(239, 68, 68, .5); }

.pm-head { display: flex; gap: 18px; align-items: center; }
.pm-ring { position: relative; width: 96px; height: 96px; flex-shrink: 0; }
.pm-ring-svg { width: 100%; height: 100%; transform: rotate(-90deg); }
.pm-ring-svg circle { fill: none; stroke-width: 8; }
.pm-ring-svg .track { stroke: #1e2533; }
.pm-ring-svg .bar {
  stroke: #3b82f6; stroke-linecap: round;
  stroke-dasharray: 326.73; transition: stroke-dashoffset .6s ease;
  filter: drop-shadow(0 0 6px rgba(59, 130, 246, .6));
}
.st-done .pm-ring-svg .bar { stroke: #10b981; filter: drop-shadow(0 0 6px rgba(16, 185, 129, .6)); }
.st-error .pm-ring-svg .bar { stroke: #ef4444; }
.pm-orbit { position: absolute; inset: -4px; animation: pm-spin 2.4s linear infinite; }
.pm-orbit span { position: absolute; width: 7px; height: 7px; border-radius: 50%; background: #60a5fa; top: 0; left: 50%; margin-left: -3.5px; box-shadow: 0 0 10px #60a5fa; }
.pm-orbit span:nth-child(2) { transform: rotate(120deg); transform-origin: 3.5px 52px; background: #34d399; box-shadow: 0 0 10px #34d399; }
.pm-orbit span:nth-child(3) { transform: rotate(240deg); transform-origin: 3.5px 52px; background: #fbbf24; box-shadow: 0 0 10px #fbbf24; }
@keyframes pm-spin { to { transform: rotate(360deg); } }
.pm-ring-center { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; }
.pm-ring-center b { font-family: 'JetBrains Mono', monospace; font-size: 1.35rem; color: #e2e8f0; }
.pm-ring-center small { font-size: .7rem; color: #8892a4; margin-left: 1px; }
.pm-check { width: 44px; height: 44px; }
.pm-check path { fill: none; stroke: #10b981; stroke-width: 5; stroke-linecap: round; stroke-linejoin: round; stroke-dasharray: 40; stroke-dashoffset: 40; animation: pm-draw .5s .1s forwards; }
@keyframes pm-draw { to { stroke-dashoffset: 0; } }
.pm-x { color: #ef4444; font-size: 2rem; font-weight: 800; }

.pm-titles { min-width: 0; }
.pm-title { font-size: 1.15rem; font-weight: 800; color: #e2e8f0; letter-spacing: -.3px; }
.pm-sub { font-size: .82rem; color: #8892a4; margin-top: 4px; line-height: 1.5; }
.pm-meta { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; font-size: .74rem; color: #a0aec0; font-family: 'JetBrains Mono', monospace; }
.pm-live { display: inline-flex; align-items: center; gap: 5px; color: #fca5a5; }
.pm-live i { width: 7px; height: 7px; border-radius: 50%; background: #ef4444; animation: pm-blink 1.2s infinite; }
@keyframes pm-blink { 50% { opacity: .25; } }

.pm-now { background: #0b0f14; border: 1px solid #1e2533; border-radius: 10px; padding: 12px 14px; }
.pm-now-label { font-size: .68rem; color: #64748b; letter-spacing: .5px; margin-bottom: 4px; }
.pm-now-text { color: #e2e8f0; font-size: .9rem; line-height: 1.55; min-height: 1.4em; word-break: keep-all; }
.st-error .pm-now-text { color: #fca5a5; }
.pm-dots i { font-style: normal; animation: pm-dot 1.4s infinite; opacity: 0; }
.pm-dots i:nth-child(2) { animation-delay: .2s; } .pm-dots i:nth-child(3) { animation-delay: .4s; }
@keyframes pm-dot { 30%, 60% { opacity: 1; } }
.pm-bar { height: 6px; background: #1e2533; border-radius: 999px; overflow: hidden; margin-top: 10px; }
.pm-bar-fill {
  height: 100%; border-radius: 999px; transition: width .6s ease;
  background: linear-gradient(90deg, #2563eb, #10b981);
  background-size: 200% 100%;
  animation: pm-flow 2s linear infinite;
  position: relative; overflow: hidden;
}
.pm-bar-fill::after {
  content: ''; position: absolute; inset: 0;
  background: repeating-linear-gradient(-45deg, rgba(255,255,255,.18) 0 8px, transparent 8px 16px);
  animation: pm-stripes .8s linear infinite;
}
@keyframes pm-flow { to { background-position: -200% 0; } }
@keyframes pm-stripes { to { background-position: 22px 0; } }

.pm-stages { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 6px 14px; }
.pm-stages li { display: flex; align-items: center; gap: 8px; font-size: .82rem; color: #64748b; padding: 4px 0; }
.pm-dot { width: 22px; height: 22px; border-radius: 50%; border: 1.5px solid #2a3041; display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0; font-size: .68rem; }
.pm-dot em { font-style: normal; font-family: 'JetBrains Mono', monospace; }
.pm-dot svg { width: 14px; height: 14px; }
.pm-dot svg path { fill: none; stroke: #fff; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; stroke-dasharray: 20; stroke-dashoffset: 20; animation: pm-draw .35s forwards; }
.s-running { color: #e2e8f0 !important; }
.s-running .pm-dot { border-color: #3b82f6; color: #93c5fd; box-shadow: 0 0 0 0 rgba(59, 130, 246, .6); animation: pm-pulse 1.5s infinite; }
@keyframes pm-pulse { 70% { box-shadow: 0 0 0 8px rgba(59, 130, 246, 0); } 100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); } }
.s-done { color: #a7f3d0 !important; }
.s-done .pm-dot { background: #10b981; border-color: #10b981; }
.s-error { color: #fca5a5 !important; }
.s-error .pm-dot { background: #ef4444; border-color: #ef4444; color: #fff; }
.pm-stage-note { margin-left: auto; font-size: .7rem; color: #64748b; font-family: 'JetBrains Mono', monospace; }

.pm-log { max-height: 170px; overflow-y: auto; background: #070a0f; border: 1px solid #1e2533; border-radius: 10px; padding: 8px 10px; font-family: 'JetBrains Mono', 'Noto Sans KR', monospace; font-size: .74rem; }
.pm-line { display: flex; gap: 10px; padding: 2px 0; color: #94a3b8; }
.pm-line .t { color: #475569; flex-shrink: 0; }
.pm-line.ok .x { color: #6ee7b7; }
.pm-line.err .x { color: #fca5a5; }
.pm-line:last-child .x { color: #e2e8f0; }

.pm-foot { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.pm-hint { font-size: .74rem; color: #64748b; }
.pm-btns { display: flex; gap: 8px; }
.pm-btn { background: transparent; border: 1px solid #2a3041; color: #c9d4e0; border-radius: 8px; padding: 7px 14px; font-size: .8rem; cursor: pointer; font-family: inherit; }
.pm-btn:hover { border-color: #3b82f6; color: #fff; }
.pm-btn.primary { background: #3b82f6; border-color: #3b82f6; color: #fff; }

.pm-chip {
  position: fixed; right: 18px; bottom: 18px; z-index: 9998; display: flex; align-items: center; gap: 10px;
  background: #111823; border: 1px solid #2a3041; color: #e2e8f0; border-radius: 999px; padding: 10px 16px;
  box-shadow: 0 12px 30px rgba(0, 0, 0, .45); cursor: pointer; font-family: inherit; font-size: .82rem;
}
.pm-chip-spin { width: 14px; height: 14px; border: 2px solid rgba(59, 130, 246, .25); border-top-color: #3b82f6; border-radius: 50%; animation: pm-spin .8s linear infinite; }

.pm-fade-enter-active, .pm-fade-leave-active { transition: opacity .25s ease; }
.pm-fade-enter-from, .pm-fade-leave-to { opacity: 0; }
.pm-swap-enter-active, .pm-swap-leave-active { transition: all .22s ease; }
.pm-swap-enter-from { opacity: 0; transform: translateY(6px); }
.pm-swap-leave-to { opacity: 0; transform: translateY(-6px); }
.pm-line-enter-active { transition: all .3s ease; }
.pm-line-enter-from { opacity: 0; transform: translateX(-8px); }

@media (max-width: 560px) {
  .pm-head { flex-direction: column; align-items: flex-start; }
  .pm-ring { width: 80px; height: 80px; }
}
@media (prefers-reduced-motion: reduce) {
  .pm-orbit, .pm-bar-fill, .pm-bar-fill::after, .s-running .pm-dot, .pm-live i { animation: none; }
}
</style>
