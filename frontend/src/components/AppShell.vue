<template>
  <div class="shell">
    <header class="topbar">
      <div class="tb-left">
        <div class="brand" role="button" tabindex="0" @click="go('/')" @keydown.enter="go('/')">
          <svg class="logo" viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
            <defs>
              <linearGradient :id="gid" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#2563eb" /><stop offset="100%" stop-color="#10b981" />
              </linearGradient>
            </defs>
            <rect x="1" y="1" width="30" height="30" rx="8" :fill="`url(#${gid})`" />
            <polyline points="7,22 12.5,16 16.5,19.5 24.5,10.5" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
            <polyline points="19.8,10.5 24.5,10.5 24.5,15.2" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <div class="brand-text">
            <span class="wordmark">WeAid<b>Stock</b></span>
            <span class="brand-sub">{{ sub }}</span>
          </div>
        </div>
        <nav class="tb-nav" aria-label="주요 메뉴">
          <button v-for="n in NAV" :key="n.path" class="nav-btn" :class="{ active: isActive(n) }"
            :aria-current="isActive(n) ? 'page' : undefined" @click="go(n.path)">{{ n.label }}</button>
        </nav>
      </div>
      <div class="tb-right"><slot name="right" /></div>
    </header>
    <main class="wrap"><slot /></main>
  </div>
</template>

<script setup>
import { useRouter, useRoute } from 'vue-router'

defineProps({ sub: { type: String, default: 'AI 주식 분석 터미널' } })
const router = useRouter()
const route = useRoute()
const gid = `shell-logo-${Math.random().toString(36).slice(2, 8)}`
const NAV = [
  { path: '/', label: '대시보드' },
  { path: '/stock', label: '5단계 AI 분석' },
  { path: '/reports', label: '보관함' },
  { path: '/watchlist', label: '관심종목' },
  { path: '/performance', label: '성과' },
  { path: '/ontology', label: '온톨로지' },
  { path: '/lab', label: '시뮬레이션 랩' },
  { path: '/settings', label: '설정' },
]
const isActive = (n) => (n.path === '/' ? route.path === '/' : route.path.startsWith(n.path))
const go = (p) => { if (route.path !== p) router.push(p) }
</script>

<style scoped>
.shell {
  --bg: #0b0f14; --panel: #0f1419; --panel-2: #121821; --border: #2a3041; --subtle: #1e2533;
  --text: #e2e8f0; --text-2: #c9d4e0; --muted: #8892a4; --primary: #3b82f6;
  --red: #ef4444; --amber: #f59e0b; --green: #10b981;
  --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: 'Noto Sans KR', 'Inter', -apple-system, 'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
  min-height: 100vh; background: var(--bg); color: var(--text); font-family: var(--sans);
  font-size: 13px; line-height: 1.5; color-scheme: dark; overflow-x: clip;
}
.shell :deep(*), .shell :deep(*::before), .shell :deep(*::after) { box-sizing: border-box; }
.topbar {
  position: sticky; top: 0; z-index: 50; display: flex; align-items: center; justify-content: space-between;
  gap: 16px; min-height: 54px; padding: 8px 20px; background: rgba(11, 15, 20, .92);
  backdrop-filter: blur(10px); border-bottom: 1px solid var(--border);
}
.tb-left { display: flex; align-items: center; gap: 22px; min-width: 0; }
.brand { display: flex; align-items: center; gap: 10px; cursor: pointer; flex: none; }
.brand-text { display: flex; flex-direction: column; line-height: 1.15; }
.wordmark { font-family: 'Inter', var(--sans); font-size: 16px; font-weight: 700; letter-spacing: -.02em; }
.wordmark b { color: #60a5fa; }
.brand-sub { font-size: 10.5px; color: var(--muted); }
.tb-nav { display: flex; gap: 2px; overflow-x: auto; scrollbar-width: none; min-width: 0; }
.tb-nav::-webkit-scrollbar { display: none; }
.nav-btn { font: inherit; color: var(--muted); background: none; border: 0; cursor: pointer; padding: 7px 12px; border-radius: 6px; font-size: 12.5px; font-weight: 500; white-space: nowrap; }
.nav-btn:hover { color: var(--text); background: var(--subtle); }
.nav-btn.active { color: var(--text); background: var(--subtle); box-shadow: inset 0 -2px 0 var(--primary); }
.tb-right { display: flex; align-items: center; gap: 8px; flex: none; }
.wrap { max-width: 1320px; margin: 0 auto; padding: 22px 20px 60px; }
@media (max-width: 720px) {
  .topbar { padding: 8px 12px; flex-wrap: wrap; }
  .tb-left { gap: 10px; width: 100%; }
  .brand-text { display: none; }
  .wrap { padding: 16px 12px 60px; }
}
</style>
