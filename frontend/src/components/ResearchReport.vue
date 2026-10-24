<template>
  <div v-if="report" ref="rootEl" class="rr rr-print-root">
    <!-- 도구 모음 (인쇄 제외) -->
    <div class="rr-toolbar rr-no-print">
      <div class="rr-tb-left">
        <span class="rr-tb-cap">증권사 리서치 리포트 양식</span>
        <span v-if="genText" class="rr-tb-gen">{{ genText }}</span>
      </div>
      <div class="rr-tb-right">
        <button type="button" class="rr-btn" @click="downloadMarkdown">Markdown 다운로드</button>
        <button type="button" class="rr-btn rr-btn-primary" @click="printPdf">PDF 저장</button>
      </div>
    </div>

    <article class="rr-paper">
      <!-- ── 헤더 ── -->
      <header class="rr-head">
        <div class="rr-head-top">
          <span class="rr-brand">WeAidStock 리서치</span>
          <span class="rr-kind">기업 분석 · AI 리서치 노트</span>
          <span class="rr-date">{{ dateText }}</span>
        </div>
        <div class="rr-head-main">
          <div class="rr-title-block">
            <h1 class="rr-company">{{ companyName }}<span class="rr-code">{{ ticker }}</span></h1>
            <p v-if="headline" class="rr-headline">{{ headline }}</p>
          </div>
          <div class="rr-rating" :class="ratingClass">
            <span class="rr-rating-cap">투자의견</span>
            <span class="rr-rating-val">{{ ratingText }}</span>
          </div>
        </div>
        <dl class="rr-kpis">
          <div class="rr-kpi"><dt>목표주가</dt><dd>{{ won(summary.target_price) }}</dd></div>
          <div class="rr-kpi"><dt>현재주가</dt><dd>{{ won(lastClose) }}</dd></div>
          <div class="rr-kpi"><dt>상승여력</dt><dd :class="signClass(summary.upside_pct)">{{ signedPct(summary.upside_pct) }}</dd></div>
          <div class="rr-kpi"><dt>52주 최고 / 최저</dt><dd class="rr-kpi-sm">{{ w52Text }}</dd></div>
          <div class="rr-kpi"><dt>PER / PBR</dt><dd class="rr-kpi-sm">{{ multText }}</dd></div>
          <div class="rr-kpi"><dt>에이전트 합의</dt><dd class="rr-kpi-sm">{{ consensusText }}</dd></div>
        </dl>
        <div v-if="consensus" class="rr-cons">
          <div class="rr-cons-head">
            <span class="rr-cons-title">증권사 컨센서스</span>
            <span v-if="consensus.date" class="rr-cons-date">기준일 {{ consensus.date }}</span>
          </div>
          <dl class="rr-cons-grid">
            <div class="rr-cons-item">
              <dt>목표주가 평균</dt>
              <dd>{{ won(consensus.target_mean) }} <small :class="signClass(consensus.upside_pct)">{{ consensus.upsideText }}</small></dd>
            </div>
            <div class="rr-cons-item"><dt>투자의견 평균</dt><dd>{{ consensus.recommText }}</dd></div>
            <div class="rr-cons-item"><dt>선행 PER</dt><dd>{{ consensus.fwdPerText }}</dd></div>
            <div class="rr-cons-item"><dt>선행 EPS</dt><dd>{{ won(consensus.fwd_eps) }}</dd></div>
          </dl>
          <p v-if="consensus.gapText" class="rr-cons-gap">{{ consensus.gapText }}</p>
          <p v-if="consensus.note" class="rr-cons-note">{{ consensus.note }}</p>
        </div>
        <div class="rr-meta">
          <span v-if="asOf">기준일 {{ asOf }}</span>
          <span>보고서 ID {{ reportIdText }}</span>
          <span v-if="!isV2" class="rr-legacy">이전 형식 보고서</span>
        </div>
      </header>

      <!-- ── 투자 요약 박스 ── -->
      <section v-if="keyBox" class="rr-keybox">
        <div class="rr-sec-head">
          <h2 class="rr-h2">투자 요약</h2>
          <span v-if="keyBox.badge" class="rr-badge" :class="keyBox.badge.cls" :title="keyBox.badge.tip">{{ keyBox.badge.label }}</span>
        </div>
        <div class="rr-md" v-html="keyBox.summaryHtml" @click="onMdClick" @keydown.enter="onMdClick"></div>
        <ul v-if="keyBox.highlights.length" class="rr-key-list">
          <li v-for="(h, i) in keyBox.highlights" :key="i" v-html="h" @click="onMdClick"></li>
        </ul>
        <p v-if="researchSummaryHtml" class="rr-research-sum">
          <b>뉴스·공시 리서치</b>
          <span v-html="researchSummaryHtml" @click="onMdClick"></span>
        </p>
      </section>

      <!-- ── 본문 섹션 ── -->
      <section v-for="(sec, i) in bodySections" :key="sec.key" class="rr-sec">
        <div class="rr-sec-head">
          <h2 class="rr-h2"><span class="rr-sec-no">{{ i + 1 }}</span>{{ sec.title }}</h2>
          <span v-if="sec.badge" class="rr-badge" :class="sec.badge.cls" :title="sec.badge.tip">{{ sec.badge.label }}</span>
        </div>

        <!-- 그림: 주가·거래량 -->
        <figure v-if="sec.key === 'technical_analysis' && priceChart" class="rr-fig">
          <figcaption class="rr-figcap">
            <span>그림 {{ figNo.price }}. 주가·거래량 추이 (최근 {{ priceChart.n }}거래일)</span>
            <span class="rr-legend">
              <i class="rr-lg rr-lg-up"></i>상승 <i class="rr-lg rr-lg-down"></i>하락
              <i class="rr-lg rr-lg-ma20"></i>MA20 <i class="rr-lg rr-lg-ma60"></i>MA60
            </span>
          </figcaption>
          <div class="rr-scroll">
            <svg class="rr-svg rr-svg-price" :viewBox="`0 0 ${priceChart.W} ${priceChart.H}`" role="img"
                 :aria-label="`${companyName} 주가 차트`">
              <g v-for="t in priceChart.yTicks" :key="'y' + t.v">
                <line :x1="priceChart.padL" :x2="priceChart.plotR" :y1="t.y" :y2="t.y" class="rr-grid" />
                <text :x="priceChart.plotR + 6" :y="t.y + 3.5" class="rr-axis-t">{{ t.label }}</text>
              </g>
              <g v-for="t in priceChart.xTicks" :key="'x' + t.i">
                <line :x1="t.x" :x2="t.x" :y1="priceChart.padT" :y2="priceChart.volBottom" class="rr-grid rr-grid-v" />
                <text :x="t.x" :y="priceChart.H - 4" class="rr-axis-t" :text-anchor="t.anchor">{{ t.label }}</text>
              </g>
              <!-- 거래량 -->
              <text :x="priceChart.padL + 2" :y="priceChart.volTop + 9" class="rr-axis-t">거래량 (최대 {{ priceChart.vmaxLabel }})</text>
              <rect v-for="b in priceChart.vols" :key="'v' + b.i" :x="b.x" :y="b.y" :width="b.w" :height="b.h"
                    :class="b.up ? 'rr-vol-up' : 'rr-vol-down'" />
              <!-- 캔들 또는 종가선 -->
              <template v-if="priceChart.candleMode">
                <g v-for="c in priceChart.candles" :key="'c' + c.i" :class="c.up ? 'rr-c-up' : 'rr-c-down'">
                  <line :x1="c.x" :x2="c.x" :y1="c.yh" :y2="c.yl" class="rr-wick" />
                  <rect :x="c.bx" :y="c.yt" :width="c.bw" :height="c.bh" class="rr-body" />
                </g>
              </template>
              <polyline v-else :points="priceChart.closePts" class="rr-close-line" />
              <path v-if="priceChart.ma20Path" :d="priceChart.ma20Path" class="rr-ma20" />
              <path v-if="priceChart.ma60Path" :d="priceChart.ma60Path" class="rr-ma60" />
              <!-- 현재가 -->
              <line :x1="priceChart.padL" :x2="priceChart.plotR" :y1="priceChart.lastY" :y2="priceChart.lastY" class="rr-last-line" />
              <rect :x="priceChart.plotR + 1" :y="priceChart.lastY - 8" :width="priceChart.padR - 2" height="16" rx="3" class="rr-last-box" />
              <text :x="priceChart.plotR + 5" :y="priceChart.lastY + 3.5" class="rr-last-t">{{ priceChart.lastLabel }}</text>
            </svg>
          </div>
          <div class="rr-figsrc">자료: 2단계 수집 주가(네이버 증권·KRX), WeAidStock</div>
        </figure>

        <!-- 그림: 에이전트 투표 -->
        <figure v-if="sec.key === 'consensus_forecast' && voteChart" class="rr-fig">
          <figcaption class="rr-figcap">
            <span>그림 {{ figNo.vote }}. AI 에이전트 투표 분포 ({{ voteChart.total }}명)</span>
            <span class="rr-legend">
              <i class="rr-lg rr-lg-buy"></i>매수 <i class="rr-lg rr-lg-hold"></i>보합 <i class="rr-lg rr-lg-sell"></i>매도
            </span>
          </figcaption>
          <div class="rr-vote-grid">
            <svg class="rr-svg rr-svg-donut" viewBox="0 0 42 42" role="img" aria-label="투표 비중 도넛 차트">
              <circle cx="21" cy="21" r="15.915" class="rr-donut-bg" />
              <circle v-for="s in voteChart.segments" :key="s.key" cx="21" cy="21" r="15.915" :class="['rr-donut-seg', s.cls]"
                      :stroke-dasharray="s.dash" :stroke-dashoffset="s.offset" />
              <text x="21" y="20" text-anchor="middle" class="rr-donut-big">{{ voteChart.lead.label }} {{ voteChart.lead.pct }}%</text>
              <text x="21" y="25.5" text-anchor="middle" class="rr-donut-sm">{{ consensusText }}</text>
            </svg>
            <div v-if="voteChart.types.length" class="rr-scroll rr-scroll-sm">
              <svg class="rr-svg rr-svg-types" :viewBox="`0 0 ${voteChart.TW} ${voteChart.TH}`" role="img" aria-label="성향별 투표 막대 차트">
                <text x="0" y="12" class="rr-axis-t">성향별 투표(명)</text>
                <g v-for="t in voteChart.types" :key="t.key">
                  <text :x="0" :y="t.y + 13" class="rr-type-label">{{ t.label }}</text>
                  <g v-for="p in t.parts" :key="p.key">
                    <rect :x="p.x" :y="t.y" :width="p.w" height="18" :class="p.cls" />
                    <text v-if="p.showLabel" :x="p.x + p.w / 2" :y="t.y + 13" text-anchor="middle" class="rr-bar-t">{{ p.n }}</text>
                  </g>
                  <text :x="voteChart.TW - 2" :y="t.y + 13" text-anchor="end" class="rr-axis-t">{{ t.total }}명</text>
                </g>
              </svg>
            </div>
          </div>
          <div class="rr-figsrc">자료: WeAidStock 에이전트 시뮬레이션</div>
        </figure>

        <!-- 그림: 밸류에이션 밴드 -->
        <figure v-if="sec.key === 'valuation' && bandChart" class="rr-fig">
          <figcaption class="rr-figcap">
            <span>그림 {{ figNo.band }}. 밸류에이션 밴드와 목표주가</span>
            <span class="rr-legend">
              <i class="rr-lg rr-lg-cur"></i>현재가 {{ won(bandChart.cur) }}
              <i class="rr-lg" :class="bandChart.tgtUp ? 'rr-lg-tgt-up' : 'rr-lg-tgt-down'"></i>목표주가 {{ won(bandChart.tgt) }}
              <template v-if="bandChart.fair"><i class="rr-lg rr-lg-fair"></i>적정주가 {{ won(bandChart.fair) }}</template>
              <template v-if="bandChart.cons"><i class="rr-lg rr-lg-cons"></i>컨센서스 {{ won(bandChart.cons) }}</template>
            </span>
          </figcaption>
          <div class="rr-scroll">
            <svg class="rr-svg rr-svg-band" :viewBox="`0 0 ${bandChart.W} ${bandChart.H}`" role="img" aria-label="밸류에이션 밴드 차트">
              <g v-for="t in bandChart.ticks" :key="'bt' + t.v">
                <line :x1="t.x" :x2="t.x" :y1="bandChart.T" :y2="bandChart.axisY" class="rr-grid rr-grid-v" />
                <text :x="t.x" :y="bandChart.H - 6" text-anchor="middle" class="rr-axis-t">{{ t.label }}</text>
              </g>
              <g v-for="r in bandChart.rows" :key="r.key">
                <text x="0" :y="r.y + 13" class="rr-band-label">{{ r.label }}</text>
                <text x="0" :y="r.y + 26" class="rr-axis-t">{{ r.sub }}</text>
                <rect :x="r.x1" :y="r.y + 8" :width="r.w" height="14" rx="2" :class="['rr-band-bar', r.cls]" />
                <line v-if="r.xm != null" :x1="r.xm" :x2="r.xm" :y1="r.y + 6" :y2="r.y + 24" class="rr-band-mid" />
              </g>
              <line :x1="bandChart.xCur" :x2="bandChart.xCur" :y1="bandChart.T - 6" :y2="bandChart.axisY" class="rr-mk-cur" />
              <line v-if="bandChart.xFair != null" :x1="bandChart.xFair" :x2="bandChart.xFair" :y1="bandChart.T - 6" :y2="bandChart.axisY" class="rr-mk-fair" />
              <line v-if="bandChart.xCons != null" :x1="bandChart.xCons" :x2="bandChart.xCons" :y1="bandChart.T - 6" :y2="bandChart.axisY" class="rr-mk-cons" />
              <g v-if="bandChart.xTgt != null" :class="bandChart.tgtUp ? 'rr-mk-tgt-up' : 'rr-mk-tgt-down'">
                <line :x1="bandChart.xTgt" :x2="bandChart.xTgt" :y1="bandChart.T - 6" :y2="bandChart.axisY" class="rr-mk-tgt" />
                <polygon :points="bandChart.tgtDiamond" class="rr-mk-tgt-d" />
              </g>
            </svg>
          </div>
          <div class="rr-figsrc">자료: 2단계 재무 지표·주가{{ consensus ? ', 증권사 컨센서스(네이버 증권)' : '' }}, WeAidStock 밸류에이션 산출</div>
        </figure>

        <div class="rr-md" v-html="sec.html" @click="onMdClick" @keydown.enter="onMdClick"></div>
      </section>

      <!-- ── 참고 자료 ── -->
      <section v-if="sources.length" class="rr-sec rr-sources">
        <div class="rr-sec-head"><h2 class="rr-h2">참고 자료</h2></div>
        <ol class="rr-src-list">
          <li v-for="s in sources" :key="s.id" :id="srcDomId(s.id)" :class="{ 'rr-src-hl': highlighted === s.id }">
            <span class="rr-src-id">{{ s.id }}</span>
            <span class="rr-src-body">
              <a v-if="s.safeUrl" :href="s.safeUrl" target="_blank" rel="noopener noreferrer" class="rr-src-link">{{ s.label }}</a>
              <span v-else>{{ s.label }}</span>
              <span v-if="s.meta" class="rr-src-meta">{{ s.meta }}</span>
              <span v-if="s.claim && s.claim !== s.label" class="rr-src-claim">{{ s.claim }}</span>
              <span v-if="s.safeUrl" class="rr-src-url">{{ s.safeUrl }}</span>
            </span>
          </li>
        </ol>
      </section>

      <!-- ── 증권사 리포트 ── -->
      <section v-if="brokerReports.length" class="rr-sec rr-brokers">
        <div class="rr-sec-head"><h2 class="rr-h2">증권사 리포트</h2></div>
        <ol class="rr-src-list">
          <li v-for="b in brokerReports" :key="b.key" :id="b.domId" :class="{ 'rr-src-hl': b.id && highlighted === b.id }">
            <span class="rr-src-id">{{ b.id || '-' }}</span>
            <span class="rr-src-body">
              <a v-if="b.safeUrl" :href="b.safeUrl" target="_blank" rel="noopener noreferrer" class="rr-src-link">{{ b.title }}</a>
              <span v-else class="rr-src-title">{{ b.title }}</span>
              <span class="rr-src-meta">{{ b.meta }}</span>
              <span v-if="b.safeUrl" class="rr-src-url">{{ b.safeUrl }}</span>
            </span>
          </li>
        </ol>
      </section>

      <!-- ── 근거 데이터(F#·C#) ── -->
      <section v-if="dataFacts.length" class="rr-sec rr-facts">
        <div class="rr-sec-head"><h2 class="rr-h2">근거 데이터</h2></div>
        <ul class="rr-fact-list">
          <li v-for="f in dataFacts" :key="f.id" :id="srcDomId(f.id)" :class="{ 'rr-src-hl': highlighted === f.id }">
            <span class="rr-src-id">{{ f.id }}</span><span>{{ f.text }}</span>
          </li>
        </ul>
      </section>

      <footer class="rr-disclaimer">{{ disclaimer }}</footer>
    </article>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  report: { type: Object, default: null },
  reportId: { type: String, default: '' }
})

const rootEl = ref(null)
const highlighted = ref('')
const uid = Math.random().toString(36).slice(2, 8)

// ─── 상수 ──────────────────────────────────────────────────────────────
const OLD_TITLES = {
  overview: '종목 개요', simulation_summary: '시뮬레이션 결과 요약', sentiment_analysis: '뉴스 센티먼트 분석',
  technical_analysis: '기술적 분석', consensus_forecast: '에이전트 합의 예측', risk_factors: '리스크 요인',
  investment_opinion: '종합 투자 의견', investment_summary: '투자 요약', investment_points: '투자 포인트',
  earnings_analysis: '실적 분석', valuation: '밸류에이션'
}
const TYPE_KO = { aggressive: '공격형', moderate: '중립형', conservative: '보수형', unknown: '기타' }
const DEFAULT_DISCLAIMER = '본 보고서는 AI 시뮬레이션 결과이며 투자 조언이 아닙니다. 투자 결정과 그 책임은 투자자 본인에게 있습니다.'

// ─── 포맷 ──────────────────────────────────────────────────────────────
const num = (v) => (v === null || v === undefined || v === '' || !isFinite(Number(v)) ? null : Number(v))
const fmtN = (v) => { const n = num(v); return n === null ? '-' : Math.round(n).toLocaleString('ko-KR') }
const won = (v) => (num(v) === null ? '-' : `${fmtN(v)}원`)
const signedPct = (v) => { const n = num(v); return n === null ? '-' : `${n > 0 ? '+' : ''}${n.toFixed(1)}%` }
const signClass = (v) => { const n = num(v); return n === null || n === 0 ? '' : n > 0 ? 'rr-up' : 'rr-down' }
const mult = (v) => { const n = num(v); return n === null ? '-' : n.toFixed(2) }

// ─── 안전한 마크다운 렌더러 (HTML 이스케이프 후 제한된 태그만 생성) ──────────
function esc(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}
function safeUrl(u) {
  const s = String(u || '').trim()
  return /^https?:\/\/[^\s<>"']+$/i.test(s) ? s : ''
}
const factMap = computed(() => {
  const m = {}
  for (const f of props.report?.facts || []) if (f && f.id) m[f.id] = f
  return m
})
const sourceMap = computed(() => {
  const m = {}
  for (const s of props.report?.sources || []) if (s && s.id) m[s.id] = s
  return m
})
function refHtml(id) {
  const src = sourceMap.value[id]
  const fact = factMap.value[id]
  if (!src && !fact) return `[${id}]`
  const tip = src ? `${src.title || src.claim || ''}${src.publisher ? ' · ' + src.publisher : ''}` : fact.text || ''
  return `<a class="rr-ref" data-ref="${esc(id)}" role="button" tabindex="0" title="${esc(tip)}">[${esc(id)}]</a>`
}
function inline(escaped) {
  const codes = []
  let t = escaped.replace(/`([^`]+)`/g, (_, c) => { codes.push(c); return `\u0000${codes.length - 1}\u0000` })
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`)
  t = t.replace(/\[([FRCKH])\s?(\d{1,3})\]/g, (_, k, n) => refHtml(k + n))
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  t = t.replace(/(^|[^*\w])\*([^*\s][^*]*?)\*(?!\*)/g, '$1<em>$2</em>')
  t = t.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[Number(i)]}</code>`)
  return t
}
const inl = (s) => inline(esc(s))
function splitRow(line) {
  let s = line.trim()
  if (s.startsWith('|')) s = s.slice(1)
  if (s.endsWith('|')) s = s.slice(0, -1)
  return s.split('|').map(c => c.trim())
}
const NUM_CELL = /^[+\-−]?[\d,.]+\s*(원|%|배|명|주|자)?$/
function tableHtml(head, rows) {
  const th = head.map(h => `<th>${inl(h)}</th>`).join('')
  const tb = rows.map(r => {
    const cells = head.map((_, i) => r[i] === undefined ? '' : r[i])
    return `<tr>${cells.map(c => `<td${NUM_CELL.test(c) ? ' class="rr-num"' : ''}>${inl(c)}</td>`).join('')}</tr>`
  }).join('')
  return `<div class="rr-table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table></div>`
}
const SEP_ROW = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/
function renderMd(md) {
  const lines = String(md || '').replace(/\r\n?/g, '\n').split('\n')
  let html = ''
  let para = []
  let i = 0
  const flush = () => {
    if (para.length) { html += `<p>${para.map(inl).join('<br>')}</p>`; para = [] }
  }
  while (i < lines.length) {
    const t = lines[i].trim()
    if (!t) { flush(); i++; continue }
    if (t.startsWith('```')) {
      flush()
      const buf = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) buf.push(lines[i++])
      i++
      html += `<pre><code>${esc(buf.join('\n'))}</code></pre>`
      continue
    }
    const hm = t.match(/^(#{1,6})\s+(.*)$/)
    if (hm) {
      flush()
      const lv = Math.min(6, Math.max(3, hm[1].length))
      html += `<h${lv}>${inl(hm[2])}</h${lv}>`
      i++
      continue
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) { flush(); html += '<hr>'; i++; continue }
    if (t.startsWith('|') && i + 1 < lines.length && SEP_ROW.test(lines[i + 1].trim())) {
      flush()
      const head = splitRow(t)
      i += 2
      const body = []
      while (i < lines.length && lines[i].trim().startsWith('|')) body.push(splitRow(lines[i++]))
      html += tableHtml(head, body)
      continue
    }
    if (t.startsWith('>')) {
      flush()
      const buf = []
      while (i < lines.length && lines[i].trim().startsWith('>')) buf.push(lines[i++].trim().replace(/^>\s?/, ''))
      html += `<blockquote>${buf.map(inl).join('<br>')}</blockquote>`
      continue
    }
    const isUl = /^[-*+]\s+/.test(t)
    const isOl = /^\d+[.)]\s+/.test(t)
    if (isUl || isOl) {
      flush()
      const items = []
      while (i < lines.length) {
        const u = lines[i].trim()
        const mm = isOl ? u.match(/^\d+[.)]\s+(.*)$/) : u.match(/^[-*+]\s+(.*)$/)
        if (!mm) break
        items.push(mm[1])
        i++
      }
      const tag = isOl ? 'ol' : 'ul'
      html += `<${tag}>${items.map(x => `<li>${inl(x)}</li>`).join('')}</${tag}>`
      continue
    }
    para.push(t)
    i++
  }
  flush()
  return html
}

// ─── 보고서 정규화 ─────────────────────────────────────────────────────
const rep = computed(() => props.report || {})
const summary = computed(() => rep.value.summary || {})
const charts = computed(() => rep.value.charts || {})
const isV2 = computed(() => Number(rep.value.report_version || 0) >= 2 || !!rep.value.summary)
const md = computed(() => String(rep.value.markdown_content || ''))

const normSections = computed(() => {
  const raw = rep.value.sections || {}
  const out = []
  for (const [key, val] of Object.entries(raw)) {
    if (!val) continue
    if (typeof val === 'string') out.push({ key, title: OLD_TITLES[key] || key, content: val, data: null, validation: null })
    else out.push({ key, title: val.title || OLD_TITLES[key] || key, content: String(val.content || ''), data: val.data || null, validation: val.validation || null })
  }
  return out
})

function badgeOf(v) {
  if (!v) return null
  const errs = Array.isArray(v.errors) ? v.errors : []
  if (v.engine === 'llm' && v.valid) {
    const fixes = Math.max(0, Number(v.attempts || 1) - 1)
    return {
      cls: 'rr-badge-ok',
      label: fixes ? `검증 통과 · 수정 ${fixes}회` : '검증 통과',
      tip: `LLM 생성 · 스키마 검증 통과 (시도 ${v.attempts || 1}회)` + (errs.length ? `\n수정 이력:\n${errs.join('\n')}` : '')
    }
  }
  return {
    cls: 'rr-badge-rule',
    label: '규칙 기반 대체',
    tip: (v.fallback_reason || '데이터 기반 규칙으로 작성') + (errs.length ? `\n${errs.slice(-3).join('\n')}` : '')
  }
}

const summarySection = computed(() => normSections.value.find(s => s.key === 'investment_summary' && s.data) || null)
const keyBox = computed(() => {
  const s = summarySection.value
  if (!s) return null
  const d = s.data || {}
  return {
    badge: badgeOf(s.validation),
    summaryHtml: renderMd(d.summary || ''),
    highlights: (Array.isArray(d.highlights) ? d.highlights : []).map(h => inl(h))
  }
})
const researchSummaryHtml = computed(() => (rep.value.research_summary ? inl(rep.value.research_summary) : ''))

const bodySections = computed(() => {
  const list = normSections.value.filter(s => !(keyBox.value && s.key === 'investment_summary'))
  if (!list.length && md.value) {
    return [{ key: 'markdown', title: '보고서 본문', html: renderMd(md.value), badge: null }]
  }
  return list.map(s => ({ key: s.key, title: s.title, html: renderMd(s.content), badge: badgeOf(s.validation) }))
})

// 옛 보고서: 마크다운 핵심 요약 표에서 값 추출
function mdCell(label) {
  const m = md.value.match(new RegExp(label + '\\s*\\|\\s*([^|\\n]+)'))
  return m ? m[1].trim() : ''
}
const companyName = computed(() => rep.value.company_name || '종목')
const ticker = computed(() => rep.value.ticker || '')
const reportIdText = computed(() => props.reportId || rep.value.report_id || '-')
const lastClose = computed(() => {
  if (num(summary.value.last_close) !== null) return num(summary.value.last_close)
  const rows = charts.value.ohlcv || []
  if (rows.length) return num(rows[rows.length - 1].c)
  const cell = mdCell('현재 주가').replace(/[^\d.]/g, '')
  return cell ? Number(cell) : null
})
const headline = computed(() => summary.value.headline || '')
const ratingText = computed(() => summary.value.rating || '의견 없음')
const ratingClass = computed(() => ({ 매수: 'rr-rating-buy', 매도: 'rr-rating-sell', 중립: 'rr-rating-hold' }[summary.value.rating] || 'rr-rating-none'))
const consensusText = computed(() => {
  const s = summary.value.consensus_signal || normSections.value.find(x => x.key === 'consensus_forecast')?.data?.signal
  return s || mdCell('에이전트 합의') || '-'
})
const valData = computed(() => normSections.value.find(s => s.key === 'valuation')?.data || {})
const earnData = computed(() => normSections.value.find(s => s.key === 'earnings_analysis')?.data || {})
const w52Text = computed(() => {
  const vb = charts.value.valuation_band || {}
  if (num(vb.w52_high) === null) return '-'
  return `${fmtN(vb.w52_high)} / ${fmtN(vb.w52_low)}`
})
const multText = computed(() => {
  const per = num(valData.value.per ?? earnData.value.per)
  const pbr = num(valData.value.pbr ?? earnData.value.pbr)
  if (per === null && pbr === null) return '-'
  return `${per === null ? '-' : per.toFixed(2) + '배'} / ${pbr === null ? '-' : pbr.toFixed(2) + '배'}`
})
const asOf = computed(() => summary.value.as_of || '')
const dateText = computed(() => {
  const raw = rep.value.completed_at || rep.value.created_at
  const d = raw ? new Date(raw) : null
  if (!d || isNaN(d.getTime())) return ''
  return d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
})
const disclaimer = computed(() => rep.value.disclaimer || DEFAULT_DISCLAIMER)
const genText = computed(() => {
  const g = rep.value.generation
  if (!g) return ''
  return `섹션 ${Number(g.llm_sections || 0) + Number(g.rule_sections || 0)}개 · LLM ${g.llm_sections || 0} · 규칙 ${g.rule_sections || 0}`
})

const consensus = computed(() => {
  const c = rep.value.consensus
  if (!c || !c.has) return null
  const rm = num(c.recomm_mean)
  const gap = num(c.gap_pct ?? summary.value.consensus_gap_pct)
  return {
    ...c,
    upsideText: num(c.upside_pct) === null ? '' : `현재가 대비 ${signedPct(c.upside_pct)}`,
    recommText: c.recomm_label ? `${c.recomm_label}${rm === null ? '' : ` (${rm.toFixed(2)}/5)`}` : '-',
    fwdPerText: num(c.fwd_per) === null ? '-' : `${num(c.fwd_per).toFixed(2)}배${c.fwd_per_derived ? ' (산출)' : ''}`,
    gapText: gap === null ? '' : `당사 목표주가는 컨센서스 평균 대비 ${signedPct(gap)}`,
    note: c.note || summary.value.consensus_note || ''
  }
})
const brokerReports = computed(() => (rep.value.broker_reports || []).filter(b => b && b.title).map((b, i) => ({
  key: b.id || `b${i}`,
  id: b.id || '',
  domId: b.id ? srcDomId(b.id) : null,
  title: b.title,
  meta: [b.broker, b.date].filter(Boolean).join(' · '),
  safeUrl: safeUrl(b.url)
})))
const sources = computed(() => (rep.value.sources || []).filter(s => s && s.kind !== 'broker_report').map(s => ({
  id: s.id,
  label: s.title || s.claim || s.url || s.id,
  claim: s.claim || '',
  meta: [s.publisher, s.date].filter(Boolean).join(' · '),
  safeUrl: safeUrl(s.url)
})))
const dataFacts = computed(() => {
  const inSources = new Set(sources.value.map(s => s.id).concat(brokerReports.value.map(b => b.id)))
  return (rep.value.facts || []).filter(f => f && f.id && !inSources.has(f.id)).map(f => ({ id: f.id, text: f.text || '' }))
})
const srcDomId = (id) => `rr-src-${uid}-${id}`

// ─── 차트 공통 ─────────────────────────────────────────────────────────
function niceStep(range, count) {
  const raw = range / Math.max(1, count)
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const n = raw / mag
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * mag
}
function ticksOf(min, max, count) {
  if (!(max > min)) return [min]
  const st = niceStep(max - min, count)
  const out = []
  for (let v = Math.ceil(min / st) * st; v <= max + st * 1e-6; v += st) out.push(Number(v.toFixed(6)))
  return out
}
const shortDate = (d) => String(d || '').slice(2, 10).replace(/-/g, '.')

const figNo = computed(() => {
  let c = 0
  const n = {}
  for (const k of bodySections.value.map(s => s.key)) {
    if (k === 'valuation' && bandChart.value) n.band = ++c
    if (k === 'technical_analysis' && priceChart.value) n.price = ++c
    if (k === 'consensus_forecast' && voteChart.value) n.vote = ++c
  }
  return n
})

// ─── 그림: 주가·거래량 (SVG) ───────────────────────────────────────────
const priceChart = computed(() => {
  const rows = (charts.value.ohlcv || []).filter(r => num(r.c) !== null)
  if (rows.length < 2) return null
  const ma20 = charts.value.ma20 || []
  const ma60 = charts.value.ma60 || []
  const off = (charts.value.ohlcv || []).length - rows.length
  const W = 720, padL = 8, padR = 64, padT = 12, priceH = 196, gap = 16, volH = 54, axisH = 20
  const H = padT + priceH + gap + volH + axisH
  const plotR = W - padR
  const n = rows.length
  const step = (plotR - padL) / n
  const candleMode = n <= 160 && rows.every(r => num(r.o) !== null && num(r.h) !== null && num(r.l) !== null)
  const vals = []
  rows.forEach((r, i) => {
    vals.push(num(r.c))
    if (candleMode) vals.push(num(r.h), num(r.l))
    const a = num(ma20[i + off]); if (a !== null) vals.push(a)
    const b = num(ma60[i + off]); if (b !== null) vals.push(b)
  })
  let min = Math.min(...vals), max = Math.max(...vals)
  const pad = (max - min) * 0.05 || max * 0.02 || 1
  min -= pad; max += pad
  const y = (p) => padT + (max - p) / (max - min) * priceH
  const x = (i) => padL + step * (i + 0.5)
  const bw = Math.max(1, Math.min(9, step * 0.64))
  const candles = candleMode ? rows.map((r, i) => {
    const o = num(r.o), c = num(r.c)
    const yt = y(Math.max(o, c))
    return { i, x: x(i), bx: x(i) - bw / 2, bw, yh: y(num(r.h)), yl: y(num(r.l)), yt, bh: Math.max(1, y(Math.min(o, c)) - yt), up: c >= o }
  }) : []
  const closePts = rows.map((r, i) => `${x(i).toFixed(1)},${y(num(r.c)).toFixed(1)}`).join(' ')
  const pathOf = (arr) => {
    let d = '', pen = false
    rows.forEach((_, i) => {
      const v = num(arr[i + off])
      if (v === null) { pen = false; return }
      d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)} `
      pen = true
    })
    return d.trim()
  }
  const volTop = padT + priceH + gap
  const volBottom = volTop + volH
  const vmax = Math.max(0, ...rows.map(r => num(r.v) || 0))
  const vols = vmax > 0 ? rows.map((r, i) => {
    const h = (num(r.v) || 0) / vmax * (volH - 12)
    const prev = i > 0 ? num(rows[i - 1].c) : num(r.o)
    return { i, x: x(i) - bw / 2, w: bw, y: volBottom - h, h, up: num(r.c) >= (prev === null ? num(r.c) : prev) }
  }) : []
  const yTicks = ticksOf(min, max, 5).map(v => ({ v, y: y(v), label: fmtN(v) }))
  const tickIdx = [...new Set([0, Math.round((n - 1) * 0.25), Math.round((n - 1) * 0.5), Math.round((n - 1) * 0.75), n - 1])]
  const xTicks = tickIdx.map(i => ({ i, x: x(i), label: shortDate(rows[i].date), anchor: i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle' }))
  const last = num(rows[n - 1].c)
  return {
    W, H, padL, padR, padT, plotR, n, candleMode, candles, closePts,
    ma20Path: pathOf(ma20), ma60Path: pathOf(ma60), vols, volTop, volBottom,
    vmaxLabel: `${fmtN(vmax)}주`, yTicks, xTicks, lastY: y(last), lastLabel: fmtN(last)
  }
})

// ─── 그림: 에이전트 투표 (SVG 도넛 + 성향별 누적 막대) ─────────────────
const voteChart = computed(() => {
  const v = charts.value.votes
  if (!v) return null
  const bd = v.by_decision || {}
  const cnt = { buy: Number(bd.buy || 0), hold: Number(bd.hold || 0), sell: Number(bd.sell || 0) }
  const total = cnt.buy + cnt.hold + cnt.sell
  if (!total) return null
  const defs = [
    { key: 'buy', label: '매수', cls: 'rr-v-buy' },
    { key: 'hold', label: '보합', cls: 'rr-v-hold' },
    { key: 'sell', label: '매도', cls: 'rr-v-sell' }
  ]
  let cum = 0
  const segments = defs.map(d => {
    const pct = cnt[d.key] / total * 100
    const seg = { ...d, pct, dash: `${pct.toFixed(3)} ${(100 - pct).toFixed(3)}`, offset: ((25 - cum + 100) % 100).toFixed(3) }
    cum += pct
    return seg
  }).filter(s => s.pct > 0)
  const leadDef = defs.reduce((a, b) => (cnt[b.key] > cnt[a.key] ? b : a))
  const lead = { label: leadDef.label, pct: Math.round(cnt[leadDef.key] / total * 100) }
  const TW = 360, L = 50, R = 40, RH = 28
  const bwMax = TW - L - R
  const entries = Object.entries(v.by_agent_type || {})
  const types = entries.map(([t, c], idx) => {
    const tot = Number(c.buy || 0) + Number(c.hold || 0) + Number(c.sell || 0)
    let xx = L
    const parts = defs.map(d => {
      const nn = Number(c[d.key] || 0)
      const w = tot ? nn / tot * bwMax : 0
      const p = { key: d.key, cls: d.cls, n: nn, x: xx, w, showLabel: w >= 20 }
      xx += w
      return p
    }).filter(p => p.w > 0)
    return { key: t, label: TYPE_KO[t] || t, total: tot, y: 22 + idx * RH, parts }
  })
  return { total, segments, lead, types, TW, TH: 22 + types.length * RH + 4 }
})

// ─── 그림: 밸류에이션 밴드 (football field) ────────────────────────────
const bandChart = computed(() => {
  const vb = charts.value.valuation_band
  if (!vb || num(vb.last_close) === null) return null
  const rows = []
  if (num(vb.w52_low) !== null && num(vb.w52_high) !== null) {
    rows.push({ key: 'w52', label: '52주 주가 범위', sub: `${fmtN(vb.w52_low)} ~ ${fmtN(vb.w52_high)}원`, lo: num(vb.w52_low), hi: num(vb.w52_high), mid: num(vb.w52_avg), cls: 'rr-band-w52' })
  }
  for (const [k, lab] of [['per', 'PER'], ['pbr', 'PBR']]) {
    const b = vb[k]
    if (b && num(b.low) !== null && num(b.high) !== null) {
      rows.push({ key: k, label: `${vb.fwd && k === 'per' ? '후행 ' : ''}${lab} 밴드 ${mult(b.multiple_low)}~${mult(b.multiple_high)}배`, sub: `${fmtN(b.low)} ~ ${fmtN(b.high)}원`, lo: num(b.low), hi: num(b.high), mid: num(b.mid), cls: 'rr-band-mult' })
    }
  }
  const fb = vb.fwd
  if (fb && num(fb.low) !== null && num(fb.high) !== null) {
    rows.unshift({ key: 'fwd', label: `선행 EPS × PER ${mult(fb.multiple_low)}~${mult(fb.multiple_high)}배`, sub: `${fmtN(fb.low)} ~ ${fmtN(fb.high)}원`, lo: num(fb.low), hi: num(fb.high), mid: num(fb.mid), cls: 'rr-band-fwd' })
  }
  if (num(vb.support) !== null && num(vb.resistance) !== null) {
    rows.push({ key: 'tech', label: '기술적 지지~저항', sub: `${fmtN(vb.support)} ~ ${fmtN(vb.resistance)}원`, lo: num(vb.support), hi: num(vb.resistance), mid: null, cls: 'rr-band-tech' })
  }
  if (!rows.length) return null
  const cur = num(vb.last_close), tgt = num(vb.target_price), fair = num(vb.fair_value), cons = num(vb.consensus_target)
  const vals = rows.flatMap(r => [r.lo, r.hi]).concat([cur, tgt, fair, cons]).filter(v => v !== null)
  let min = Math.min(...vals), max = Math.max(...vals)
  const pad = (max - min) * 0.06 || max * 0.05
  min -= pad; max += pad
  const W = 720, L = 176, R = 18, T = 16, RH = 36, B = 24
  const axisY = T + rows.length * RH
  const H = axisY + B
  const x = (v) => L + (v - min) / (max - min) * (W - L - R)
  const out = rows.map((r, i) => {
    const x1 = x(Math.min(r.lo, r.hi)), x2 = x(Math.max(r.lo, r.hi))
    return { ...r, y: T + i * RH, x1, w: Math.max(2, x2 - x1), xm: r.mid === null || r.mid === undefined ? null : x(r.mid) }
  })
  const xTgt = tgt === null ? null : x(tgt)
  return {
    W, H, T, axisY, rows: out, cur, tgt, fair, cons, xCons: cons === null ? null : x(cons),
    tgtUp: tgt !== null && tgt >= cur,
    xCur: x(cur), xTgt, xFair: fair === null ? null : x(fair),
    tgtDiamond: xTgt === null ? '' : `${xTgt},${T - 10} ${xTgt + 5},${T - 5} ${xTgt},${T} ${xTgt - 5},${T - 5}`,
    ticks: ticksOf(min, max, 5).map(v => ({ v, x: x(v), label: fmtN(v) }))
  }
})

// ─── 근거 ID 클릭 → 출처로 이동 ────────────────────────────────────────
let hlTimer = null
function onMdClick(e) {
  const el = e.target && e.target.closest ? e.target.closest('[data-ref]') : null
  if (!el || !rootEl.value) return
  e.preventDefault()
  const id = el.getAttribute('data-ref')
  const target = rootEl.value.querySelector(`#${srcDomId(id)}`)
  if (!target) return
  target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  highlighted.value = id
  clearTimeout(hlTimer)
  hlTimer = setTimeout(() => { highlighted.value = '' }, 2200)
}

// ─── Markdown 다운로드 ─────────────────────────────────────────────────
const fileBase = computed(() => {
  const d = (rep.value.completed_at || rep.value.created_at || '').slice(0, 10).replace(/-/g, '')
  return `${companyName.value}_${ticker.value}_리서치리포트${d ? '_' + d : ''}`.replace(/[\\/:*?"<>|\s]+/g, '_')
})
function buildMarkdown() {
  if (md.value) return md.value
  const lines = [`# ${companyName.value}(${ticker.value}) 리서치 리포트`, '']
  normSections.value.forEach((s, i) => { lines.push(`## ${i + 1}. ${s.title}`, '', s.content, '') })
  if (sources.value.length) {
    lines.push('## 참고 자료', '')
    sources.value.forEach((s, i) => lines.push(`${i + 1}. [${s.id}] ${s.safeUrl ? `[${s.label}](${s.safeUrl})` : s.label}${s.meta ? ' — ' + s.meta : ''}`))
    lines.push('')
  }
  lines.push('## 면책 조항', '', `> ${disclaimer.value}`, '')
  return lines.join('\n')
}
function downloadMarkdown() {
  const blob = new Blob([buildMarkdown()], { type: 'text/markdown;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `${fileBase.value}.md`
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(a.href), 2000)
}

// ─── PDF 저장 (브라우저 인쇄 → PDF) ────────────────────────────────────
let savedTitle = null
let marked = []
let printing = false
function onBeforePrint() {
  if (printing) return   // 일부 브라우저는 beforeprint 를 두 번 보냄 → 한 번만 처리
  printing = true
  const html = document.documentElement
  html.classList.add('wa-printing')
  savedTitle = document.title
  document.title = fileBase.value
  // 보고서까지의 조상 경로만 남기고 형제 요소를 접어 빈 페이지를 방지
  marked = []
  let el = rootEl.value
  while (el && el.parentElement && el !== document.body) {
    const parent = el.parentElement
    for (const sib of Array.from(parent.children)) {
      if (sib !== el && !sib.classList.contains('wa-print-hide')) {
        sib.classList.add('wa-print-hide')
        marked.push([sib, 'wa-print-hide'])
      }
    }
    if (parent !== document.body) {
      parent.classList.add('wa-print-ancestor')
      marked.push([parent, 'wa-print-ancestor'])
    }
    el = parent
  }
  if (rootEl.value) html.classList.add('wa-print-collapsed')
}
function onAfterPrint() {
  if (!printing) return
  printing = false
  const html = document.documentElement
  html.classList.remove('wa-printing', 'wa-print-collapsed')
  for (const [node, cls] of marked) node.classList.remove(cls)
  marked = []
  if (savedTitle !== null) { document.title = savedTitle; savedTitle = null }
}
function printPdf() {
  window.print()
}
onMounted(() => {
  window.addEventListener('beforeprint', onBeforePrint)
  window.addEventListener('afterprint', onAfterPrint)
})
onBeforeUnmount(() => {
  window.removeEventListener('beforeprint', onBeforePrint)
  window.removeEventListener('afterprint', onAfterPrint)
  onAfterPrint()
  clearTimeout(hlTimer)
})
</script>

<style>
/* ResearchReport — 전역 스타일(rr- 접두사). v-html 본문과 인쇄 규칙을 위해 scoped 를 쓰지 않음 */
.rr {
  --rr-bg: #0b0f14;
  --rr-paper: #0f1419;
  --rr-panel: #121821;
  --rr-border: #2a3041;
  --rr-line: #1e2533;
  --rr-text: #e2e8f0;
  --rr-sub: #c9d4e0;
  --rr-muted: #8892a4;
  --rr-primary: #3b82f6;
  --rr-up: #ef4444;
  --rr-down: #3b82f6;
  --rr-flat: #f59e0b;
  --rr-grid: #1e2533;
  --rr-ma20: #f59e0b;
  --rr-ma60: #a78bfa;
  --rr-cons: #a78bfa;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  max-width: 100%;
  color: var(--rr-text);
  font-family: -apple-system, 'Apple SD Gothic Neo', 'Noto Sans KR', BlinkMacSystemFont, 'Segoe UI', sans-serif;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}
.rr *, .rr *::before, .rr *::after { box-sizing: border-box; }

/* 도구 모음 */
.rr-toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; }
.rr-tb-left { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.rr-tb-cap { font-size: 0.78rem; color: var(--rr-muted); }
.rr-tb-gen { font-size: 0.7rem; color: var(--rr-sub); background: var(--rr-panel); border: 1px solid var(--rr-border); padding: 2px 8px; border-radius: 999px; }
.rr-tb-right { display: flex; flex-wrap: wrap; gap: 8px; }
.rr-btn { border-radius: 8px; padding: 8px 14px; font-size: 0.82rem; font-weight: 600; cursor: pointer; font-family: inherit; background: transparent; border: 1px solid var(--rr-border); color: var(--rr-sub); }
.rr-btn:hover { border-color: var(--rr-primary); color: var(--rr-text); }
.rr-btn-primary { background: var(--rr-primary); border-color: var(--rr-primary); color: #fff; }
.rr-btn-primary:hover { background: #2563eb; color: #fff; }

/* 지면 */
.rr-paper { background: var(--rr-paper); border: 1px solid var(--rr-border); border-radius: 12px; padding: 24px 28px; display: flex; flex-direction: column; gap: 22px; min-width: 0; }

/* 헤더 */
.rr-head { display: flex; flex-direction: column; gap: 14px; border-bottom: 2px solid var(--rr-primary); padding-bottom: 16px; }
.rr-head-top { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; font-size: 0.74rem; color: var(--rr-muted); }
.rr-brand { color: var(--rr-primary); font-weight: 800; letter-spacing: 0.02em; }
.rr-date { margin-left: auto; }
.rr-head-main { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start; gap: 14px; }
.rr-title-block { min-width: 0; flex: 1 1 260px; }
.rr-company { margin: 0; font-size: 1.7rem; font-weight: 800; letter-spacing: -0.5px; color: var(--rr-text); word-break: keep-all; overflow-wrap: anywhere; }
.rr-code { font-family: 'JetBrains Mono', monospace; font-size: 0.95rem; color: var(--rr-muted); font-weight: 600; margin-left: 10px; }
.rr-headline { margin: 8px 0 0; font-size: 1.05rem; font-weight: 700; color: var(--rr-sub); line-height: 1.5; word-break: keep-all; overflow-wrap: anywhere; }
.rr-rating { display: flex; flex-direction: column; align-items: center; justify-content: center; min-width: 108px; padding: 10px 16px; border-radius: 10px; border: 2px solid var(--rr-border); background: var(--rr-panel); }
.rr-rating-cap { font-size: 0.7rem; color: var(--rr-muted); }
.rr-rating-val { font-size: 1.5rem; font-weight: 800; }
.rr-rating-buy { border-color: var(--rr-up); } .rr-rating-buy .rr-rating-val { color: var(--rr-up); }
.rr-rating-sell { border-color: var(--rr-down); } .rr-rating-sell .rr-rating-val { color: var(--rr-down); }
.rr-rating-hold { border-color: var(--rr-flat); } .rr-rating-hold .rr-rating-val { color: var(--rr-flat); }
.rr-rating-none .rr-rating-val { font-size: 1rem; color: var(--rr-muted); }
.rr-kpis { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin: 0; }
.rr-kpi { background: var(--rr-bg); border: 1px solid var(--rr-line); border-radius: 8px; padding: 9px 12px; min-width: 0; }
.rr-kpi dt { font-size: 0.7rem; color: var(--rr-muted); margin: 0 0 3px; }
.rr-kpi dd { margin: 0; font-size: 1.05rem; font-weight: 700; color: var(--rr-text); font-family: 'JetBrains Mono', monospace; overflow-wrap: anywhere; }
.rr-kpi dd.rr-kpi-sm { font-size: 0.86rem; font-family: inherit; }
.rr-up { color: var(--rr-up) !important; }
.rr-down { color: var(--rr-down) !important; }
.rr-meta { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 0.72rem; color: var(--rr-muted); }
.rr-cons { border: 1px solid var(--rr-border); border-left: 3px solid var(--rr-cons); border-radius: 8px; padding: 10px 12px; background: var(--rr-panel); }
.rr-cons-head { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 10px; margin-bottom: 8px; }
.rr-cons-title { font-size: 0.78rem; font-weight: 700; color: var(--rr-text); }
.rr-cons-date { font-size: 0.7rem; color: var(--rr-muted); }
.rr-cons-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px 12px; margin: 0; }
.rr-cons-item { min-width: 0; }
.rr-cons-item dt { font-size: 0.68rem; color: var(--rr-muted); margin: 0 0 2px; }
.rr-cons-item dd { margin: 0; font-size: 0.9rem; font-weight: 700; color: var(--rr-text); overflow-wrap: anywhere; }
.rr-cons-item dd small { display: block; font-size: 0.7rem; font-weight: 600; white-space: nowrap; }
.rr-cons-gap { margin: 8px 0 0; font-size: 0.76rem; color: var(--rr-sub); }
.rr-cons-note { margin: 6px 0 0; font-size: 0.74rem; line-height: 1.6; color: var(--rr-flat); word-break: keep-all; overflow-wrap: anywhere; }
.rr-legacy { color: var(--rr-flat); }

/* 요약 박스 */
.rr-keybox { background: var(--rr-panel); border: 1px solid var(--rr-border); border-left: 4px solid var(--rr-primary); border-radius: 10px; padding: 16px 18px; }
.rr-key-list { margin: 10px 0 0; padding-left: 20px; color: var(--rr-text); font-weight: 600; font-size: 0.9rem; line-height: 1.75; }
.rr-research-sum { margin: 12px 0 0; font-size: 0.82rem; color: var(--rr-sub); line-height: 1.7; }
.rr-research-sum b { color: var(--rr-muted); font-weight: 600; margin-right: 8px; }

/* 섹션 */
.rr-sec { min-width: 0; }
.rr-sec-head { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 10px; border-bottom: 1px solid var(--rr-border); padding-bottom: 8px; margin-bottom: 12px; }
.rr-h2 { margin: 0; font-size: 1.12rem; font-weight: 800; color: var(--rr-text); display: flex; align-items: center; gap: 8px; }
.rr-sec-no { display: inline-flex; align-items: center; justify-content: center; min-width: 24px; height: 24px; border-radius: 6px; background: var(--rr-primary); color: #fff; font-size: 0.8rem; font-family: 'JetBrains Mono', monospace; }
.rr-badge { font-size: 0.68rem; padding: 2px 9px; border-radius: 999px; border: 1px solid var(--rr-border); white-space: nowrap; cursor: help; }
.rr-badge-ok { color: #6ee7b7; border-color: rgba(16, 185, 129, 0.5); background: rgba(16, 185, 129, 0.1); }
.rr-badge-rule { color: #fcd34d; border-color: rgba(245, 158, 11, 0.5); background: rgba(245, 158, 11, 0.1); }

/* 본문 마크다운 */
.rr-md { color: var(--rr-sub); font-size: 0.92rem; line-height: 1.8; word-break: keep-all; overflow-wrap: anywhere; min-width: 0; }
.rr-md p { margin: 0 0 10px; }
.rr-md h3 { font-size: 0.98rem; color: var(--rr-text); margin: 16px 0 6px; font-weight: 700; }
.rr-md h4, .rr-md h5, .rr-md h6 { font-size: 0.92rem; color: var(--rr-text); margin: 12px 0 6px; }
.rr-md strong { color: var(--rr-text); }
.rr-md ul, .rr-md ol { margin: 6px 0 10px; padding-left: 22px; }
.rr-md li { margin: 3px 0; }
.rr-md hr { border: none; border-top: 1px solid var(--rr-border); margin: 16px 0; }
.rr-md blockquote { margin: 10px 0; padding: 8px 12px; border-left: 3px solid var(--rr-border); color: var(--rr-muted); font-size: 0.82rem; background: var(--rr-bg); border-radius: 0 6px 6px 0; }
.rr-md code { background: var(--rr-line); color: #7dd3fc; padding: 1px 6px; border-radius: 4px; font-size: 0.86em; }
.rr-md pre { background: var(--rr-bg); border: 1px solid var(--rr-border); border-radius: 8px; padding: 12px; overflow-x: auto; }
.rr-md a { color: #93c5fd; text-decoration: underline; text-underline-offset: 2px; }
.rr-table-wrap { max-width: 100%; overflow-x: auto; margin: 8px 0 12px; border: 1px solid var(--rr-line); border-radius: 8px; }
.rr-md table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.rr-md th { background: var(--rr-panel); color: var(--rr-muted); font-weight: 600; text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--rr-border); white-space: nowrap; }
.rr-md td { padding: 7px 10px; border-bottom: 1px solid var(--rr-line); color: var(--rr-sub); vertical-align: top; }
.rr-md tr:last-child td { border-bottom: none; }
.rr-md td.rr-num { text-align: right; font-family: 'JetBrains Mono', monospace; white-space: nowrap; }
.rr-ref { display: inline-block; font-family: 'JetBrains Mono', monospace; font-size: 0.72em; line-height: 1.3; padding: 0 4px; margin: 0 1px; border-radius: 4px; background: var(--rr-line); color: #93c5fd !important; text-decoration: none !important; cursor: pointer; vertical-align: 1px; }
.rr-ref:hover, .rr-ref:focus { background: var(--rr-primary); color: #fff !important; outline: none; }

/* 그림 */
.rr-fig { margin: 0 0 14px; background: var(--rr-bg); border: 1px solid var(--rr-line); border-radius: 10px; padding: 12px 14px 10px; min-width: 0; }
.rr-figcap { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 4px 12px; font-size: 0.78rem; font-weight: 700; color: var(--rr-text); margin-bottom: 8px; }
.rr-legend { font-weight: 500; color: var(--rr-muted); font-size: 0.72rem; display: inline-flex; flex-wrap: wrap; align-items: center; gap: 3px 4px; }
.rr-lg { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin: 0 2px 0 8px; vertical-align: -1px; }
.rr-lg-up, .rr-lg-buy { background: var(--rr-up); }
.rr-lg-down, .rr-lg-sell { background: var(--rr-down); }
.rr-lg-hold { background: var(--rr-flat); }
.rr-lg-ma20 { background: var(--rr-ma20); height: 3px; }
.rr-lg-ma60 { background: var(--rr-ma60); height: 3px; }
.rr-lg-cur { background: var(--rr-muted); width: 3px; }
.rr-lg-tgt-up { background: var(--rr-up); width: 8px; height: 8px; transform: rotate(45deg); }
.rr-lg-tgt-down { background: var(--rr-down); width: 8px; height: 8px; transform: rotate(45deg); }
.rr-lg-fair { background: var(--rr-flat); width: 3px; }
.rr-lg-cons { background: var(--rr-cons); width: 3px; }
.rr-figsrc { font-size: 0.68rem; color: var(--rr-muted); margin-top: 6px; }
.rr-scroll { max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }
.rr-svg { display: block; width: 100%; height: auto; }
.rr-svg-price { min-width: 560px; }
.rr-svg-band { min-width: 520px; }
.rr-svg-types { max-width: 520px; }
.rr-vote-grid { display: grid; grid-template-columns: minmax(150px, 210px) minmax(0, 1fr); gap: 16px; align-items: center; }
.rr-svg-donut { max-width: 210px; margin: 0 auto; }
.rr-scroll-sm { min-width: 0; }

.rr-svg text { font-family: inherit; }
.rr-grid { stroke: var(--rr-grid); stroke-width: 1; }
.rr-grid-v { stroke-dasharray: 2 3; }
.rr-axis-t { fill: var(--rr-muted); font-size: 10px; }
.rr-c-up .rr-wick { stroke: var(--rr-up); stroke-width: 1; }
.rr-c-up .rr-body { fill: var(--rr-up); }
.rr-c-down .rr-wick { stroke: var(--rr-down); stroke-width: 1; }
.rr-c-down .rr-body { fill: var(--rr-down); }
.rr-close-line { fill: none; stroke: var(--rr-text); stroke-width: 1.4; }
.rr-ma20 { fill: none; stroke: var(--rr-ma20); stroke-width: 1.4; }
.rr-ma60 { fill: none; stroke: var(--rr-ma60); stroke-width: 1.4; }
.rr-vol-up { fill: var(--rr-up); opacity: 0.45; }
.rr-vol-down { fill: var(--rr-down); opacity: 0.45; }
.rr-last-line { stroke: var(--rr-primary); stroke-width: 1; stroke-dasharray: 4 3; }
.rr-last-box { fill: var(--rr-primary); }
.rr-last-t { fill: #fff; font-size: 10px; font-weight: 700; }
.rr-donut-bg { fill: none; stroke: var(--rr-line); stroke-width: 6; }
.rr-donut-seg { fill: none; stroke-width: 6; }
.rr-v-buy { stroke: var(--rr-up); fill: var(--rr-up); }
.rr-v-hold { stroke: var(--rr-flat); fill: var(--rr-flat); }
.rr-v-sell { stroke: var(--rr-down); fill: var(--rr-down); }
.rr-donut-seg.rr-v-buy, .rr-donut-seg.rr-v-hold, .rr-donut-seg.rr-v-sell { fill: none; }
.rr-donut-big { fill: var(--rr-text); font-size: 4.2px; font-weight: 800; }
.rr-donut-sm { fill: var(--rr-muted); font-size: 3px; }
.rr-type-label { fill: var(--rr-sub); font-size: 13px; font-weight: 600; }
.rr-bar-t { fill: #fff; font-size: 11.5px; font-weight: 700; }
.rr-band-label { fill: var(--rr-text); font-size: 11.5px; font-weight: 700; }
.rr-band-bar { stroke: none; }
.rr-band-w52 { fill: #475569; }
.rr-band-mult { fill: #1d4ed8; }
.rr-band-tech { fill: #0f766e; }
.rr-band-fwd { fill: #7c3aed; }
.rr-mk-cons { stroke: var(--rr-cons); stroke-width: 1.6; stroke-dasharray: 6 3; }
.rr-src-title { color: var(--rr-text); font-weight: 600; }
.rr-band-mid { stroke: #fff; stroke-width: 2; }
.rr-mk-cur { stroke: var(--rr-text); stroke-width: 1.4; stroke-dasharray: 4 3; }
.rr-mk-fair { stroke: var(--rr-flat); stroke-width: 1.4; stroke-dasharray: 2 2; }
.rr-mk-tgt { stroke-width: 2; }
.rr-mk-tgt-up .rr-mk-tgt { stroke: var(--rr-up); }
.rr-mk-tgt-up .rr-mk-tgt-d { fill: var(--rr-up); }
.rr-mk-tgt-down .rr-mk-tgt { stroke: var(--rr-down); }
.rr-mk-tgt-down .rr-mk-tgt-d { fill: var(--rr-down); }

/* 출처·근거 */
.rr-src-list, .rr-fact-list { margin: 0; padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 8px; font-size: 0.82rem; }
.rr-src-list li, .rr-fact-list li { display: flex; gap: 8px; align-items: flex-start; padding: 6px 8px; border-radius: 6px; color: var(--rr-sub); min-width: 0; transition: background 0.3s; }
.rr-fact-list { font-size: 0.76rem; gap: 4px; }
.rr-src-id { flex-shrink: 0; font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; background: var(--rr-line); color: #93c5fd; padding: 1px 6px; border-radius: 4px; margin-top: 2px; }
.rr-src-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.rr-src-link { color: #93c5fd; font-weight: 600; overflow-wrap: anywhere; }
.rr-src-meta { font-size: 0.72rem; color: var(--rr-muted); }
.rr-src-claim { font-size: 0.76rem; color: var(--rr-muted); }
.rr-src-url { display: none; font-size: 0.68rem; color: var(--rr-muted); word-break: break-all; }
.rr-src-hl { background: rgba(59, 130, 246, 0.18); }

.rr-disclaimer { font-size: 0.74rem; color: var(--rr-muted); line-height: 1.6; padding: 10px 12px; border: 1px dashed var(--rr-border); border-radius: 8px; }

@media (max-width: 640px) {
  .rr-paper { padding: 16px 14px; gap: 18px; border-radius: 10px; }
  .rr-company { font-size: 1.35rem; }
  .rr-headline { font-size: 0.95rem; }
  .rr-rating { flex-direction: row; gap: 10px; min-width: 0; width: 100%; padding: 8px 12px; }
  .rr-rating-val { font-size: 1.2rem; }
  .rr-date { margin-left: 0; }
  .rr-vote-grid { grid-template-columns: 1fr; }
  .rr-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rr-cons-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rr-svg-donut { max-width: 180px; }
  .rr-fig { padding: 10px; }
  .rr-md { font-size: 0.88rem; }
  .rr-tb-right { width: 100%; }
  .rr-tb-right .rr-btn { flex: 1 1 0; }
}

/* ── 인쇄 (PDF 저장) : A4 세로 · 흰 배경 · 검정 글자 · 보고서 외 숨김 ── */
@media print {
  @page { size: A4 portrait; margin: 12mm 11mm; }
  html.wa-printing, html.wa-printing body { background: #fff !important; color: #000 !important; height: auto !important; min-height: 0 !important; overflow: visible !important; }
  html.wa-printing body * { visibility: hidden !important; }
  html.wa-printing .rr-print-root, html.wa-printing .rr-print-root * { visibility: visible !important; }
  html.wa-printing .rr-print-root { position: absolute; left: 0; top: 0; width: 100%; margin: 0; padding: 0; }
  html.wa-printing.wa-print-collapsed .rr-print-root { position: static; }
  html.wa-printing .wa-print-hide { display: none !important; }
  html.wa-printing .wa-print-ancestor { margin: 0 !important; padding: 0 !important; border: 0 !important; background: #fff !important; box-shadow: none !important; max-width: none !important; width: auto !important; min-height: 0 !important; height: auto !important; overflow: visible !important; transform: none !important; position: static !important; display: block !important; }

  .rr-print-root.rr {
    --rr-bg: #fff; --rr-paper: #fff; --rr-panel: #f3f4f6; --rr-border: #9ca3af; --rr-line: #d1d5db;
    --rr-text: #000; --rr-sub: #111; --rr-muted: #444; --rr-grid: #e5e7eb; --rr-primary: #1e3a8a;
    --rr-up: #dc2626; --rr-down: #1d4ed8; --rr-flat: #b45309; --rr-cons: #6d28d9;
    color: #000; background: #fff; gap: 0; font-size: 10.5pt;
  }
  .rr-no-print { display: none !important; }
  .rr-paper { border: none; border-radius: 0; padding: 0; gap: 14px; background: #fff; }
  .rr-head { border-bottom-color: #1e3a8a; }
  .rr-company { font-size: 20pt; }
  .rr-headline { font-size: 12pt; color: #000; }
  .rr-kpi, .rr-rating, .rr-keybox, .rr-fig, .rr-cons { background: #fff; }
  .rr-kpis, .rr-keybox, .rr-fig, .rr-table-wrap, .rr-rating, .rr-cons, .rr-src-list li { break-inside: avoid; page-break-inside: avoid; }
  .rr-cons-note { color: #7c2d12; }
  .rr-band-fwd { fill: #c4b5fd; }
  .rr-h2 { break-after: avoid; page-break-after: avoid; }
  .rr-sec-head { break-after: avoid; page-break-after: avoid; }
  .rr-md { color: #000; font-size: 10pt; line-height: 1.65; }
  .rr-md a, .rr-src-link { color: #1e3a8a; }
  .rr-md th { background: #f3f4f6; color: #111; }
  .rr-md blockquote { background: #f9fafb; }
  .rr-ref { background: none; color: #1e3a8a !important; padding: 0; font-size: 0.8em; }
  .rr-badge-ok { color: #047857; background: #ecfdf5; border-color: #6ee7b7; }
  .rr-badge-rule { color: #92400e; background: #fffbeb; border-color: #fcd34d; }
  .rr-scroll, .rr-table-wrap { overflow: visible !important; }
  .rr-svg-price, .rr-svg-band { min-width: 0 !important; }
  .rr-band-mid { stroke: #000; }
  .rr-band-w52 { fill: #9ca3af; }
  .rr-band-mult { fill: #93c5fd; }
  .rr-band-tech { fill: #5eead4; }
  .rr-src-url { display: block; }
  .rr-src-hl { background: none; }
  .rr-disclaimer { border-color: #9ca3af; color: #333; }
}
</style>
