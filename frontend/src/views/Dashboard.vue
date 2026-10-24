<template>
  <div class="dash">
    <!-- 공용 SVG 그라데이션 (스파크라인 영역 채우기) -->
    <svg class="svg-defs" width="0" height="0" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="wad-grad-up" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#ef4444" stop-opacity="0.34" />
          <stop offset="100%" stop-color="#ef4444" stop-opacity="0" />
        </linearGradient>
        <linearGradient id="wad-grad-down" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#3b82f6" stop-opacity="0.34" />
          <stop offset="100%" stop-color="#3b82f6" stop-opacity="0" />
        </linearGradient>
        <linearGradient id="wad-grad-flat" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#8892a4" stop-opacity="0.28" />
          <stop offset="100%" stop-color="#8892a4" stop-opacity="0" />
        </linearGradient>
      </defs>
    </svg>

    <!-- ═══════════════════ 상단 바 ═══════════════════ -->
    <header class="topbar">
      <div class="tb-left">
        <div class="brand" role="button" tabindex="0" aria-label="대시보드 맨 위로" @click="scrollTop" @keydown.enter="scrollTop">
          <svg class="logo" viewBox="0 0 32 32" width="30" height="30" aria-hidden="true">
            <defs>
              <linearGradient id="wad-logo-grad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#2563eb" />
                <stop offset="100%" stop-color="#10b981" />
              </linearGradient>
            </defs>
            <rect x="1" y="1" width="30" height="30" rx="8" fill="url(#wad-logo-grad)" />
            <polyline points="7,22 12.5,16 16.5,19.5 24.5,10.5" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
            <polyline points="19.8,10.5 24.5,10.5 24.5,15.2" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <div class="brand-text">
            <span class="wordmark">WeAid<b>Stock</b></span>
            <span class="brand-sub">AI 주식 분석 터미널</span>
          </div>
        </div>
        <nav class="tb-nav" aria-label="주요 메뉴">
          <button class="nav-btn active" aria-current="page">대시보드</button>
          <button class="nav-btn" @click="router.push('/stock')">5단계 AI 분석</button>
          <button class="nav-btn" @click="router.push('/reports')">보관함</button>
          <button class="nav-btn" @click="router.push('/watchlist')">관심종목</button>
          <button class="nav-btn" @click="router.push('/performance')">성과</button>
          <button class="nav-btn" @click="router.push('/ontology')">온톨로지</button>
          <button class="nav-btn" @click="router.push('/lab')">시뮬레이션 랩</button>
          <button class="nav-btn" @click="router.push('/settings')">설정</button>
        </nav>
      </div>
      <div class="tb-right">
        <Tick v-slot="{ now }">
          <span class="clock mono" :title="'한국 표준시 ' + kstDate(now) + ' ' + kstTime(now)">
            <span class="clock-date">{{ kstDate(now) }}</span>
            <span class="clock-time">{{ kstTime(now) }}</span>
            <span class="clock-tz">KST</span>
          </span>
        </Tick>
        <span class="status-pill" :class="statusTone"><i class="status-dot"></i>{{ marketStatus }}</span>
        <span class="upd mono" :class="{ busy: ovLoading }">
          <i class="upd-dot"></i>{{ updatedAt ? '갱신 ' + updatedAt : '불러오는 중' }}
        </span>
        <button class="icon-btn" :disabled="refreshing" title="전체 새로고침" aria-label="전체 새로고침" @click="refreshAll">
          <svg viewBox="0 0 24 24" width="15" height="15" :class="{ spin: refreshing }" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <polyline points="23 4 23 10 17 10" />
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
          </svg>
        </button>
      </div>
    </header>

    <!-- ═══════════════════ 티커 테이프 ═══════════════════ -->
    <div v-if="tapeItems.length" class="tape" aria-label="주요 시세 흐름">
      <div class="tape-track" :style="{ animationDuration: tapeDuration }">
        <div v-for="rep in 2" :key="rep" class="tape-group" :aria-hidden="rep === 2 ? 'true' : null">
          <span v-for="t in tapeItems" :key="rep + '-' + t.id" class="tape-item">
            <span class="tape-name">{{ t.name }}</span>
            <span class="tape-price mono">{{ t.price }}</span>
            <span class="tape-pct mono" :class="dir(t.pct)">{{ arrow(t.pct) }} {{ fmtPct(t.pct) }}</span>
          </span>
        </div>
      </div>
    </div>
    <div v-else class="tape tape-empty">
      <span v-if="ovError" class="muted small">시세 테이프를 불러오지 못했습니다 · {{ ovError }}</span>
      <span v-else class="sk sk-tape"></span>
    </div>

    <main class="main">
      <!-- ═══════════════════ ROW 1 : 지수 · 글로벌 ═══════════════════ -->
      <section class="row row-1">
        <article v-for="c in indexCards" :key="c.key" class="panel idx-card">
          <header class="panel-hd">
            <h2 class="panel-title"><i class="t-bar"></i>{{ c.name }}</h2>
            <span class="code-chip mono">{{ c.code }}</span>
            <span v-if="c.status" class="mini-status">{{ c.status }}</span>
            <button class="link-btn hd-right" @click="goIndexMovers(c.market)">주도주 보기 ›</button>
          </header>

          <div v-if="c.loading" class="idx-body">
            <div class="sk sk-h32 w50"></div>
            <div class="sk sk-h14 w35"></div>
            <div class="sk sk-chart"></div>
            <div class="sk sk-h14 w80"></div>
          </div>
          <div v-else-if="c.error" class="state-box err">
            <span>⚠ {{ c.error }}</span>
            <button class="btn-ghost sm" @click="loadOverview">다시 시도</button>
          </div>
          <div v-else class="idx-body">
            <div class="idx-quote">
              <div class="idx-main">
                <div class="idx-price mono" :class="dir(c.change)">{{ fmtNum(c.price, 2) }}</div>
                <div class="idx-chg mono">
                  <span :class="dir(c.change)">{{ arrow(c.change) }} {{ fmtAbs(c.change, 2) }}</span>
                  <span class="pct-pill" :class="dir(c.pct)">{{ fmtPct(c.pct) }}</span>
                </div>
              </div>
              <div v-if="c.sub" class="idx-sub">
                <span class="muted">{{ c.sub.name }}</span>
                <span class="mono">{{ fmtNum(c.sub.price, 2) }}</span>
                <span class="mono" :class="dir(c.sub.pct)">{{ fmtPct(c.sub.pct) }}</span>
              </div>
            </div>

            <div class="idx-chart" @pointermove="onIdxHover(c, $event)" @pointerleave="hover[c.key] = -1">
              <svg v-if="c.geom" class="fill-svg" :viewBox="`0 0 ${IDX_W} ${IDX_H}`" preserveAspectRatio="none" aria-hidden="true">
                <line v-if="c.prevY != null" class="ref-line" x1="0" :x2="IDX_W" :y1="c.prevY" :y2="c.prevY" vector-effect="non-scaling-stroke" />
                <path :d="c.geom.area" :fill="`url(#wad-grad-${c.trend})`" />
                <path :d="c.geom.line" fill="none" :stroke="COLORS[c.trend]" stroke-width="1.6" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
              </svg>
              <div v-else class="chart-empty muted small">차트 데이터 없음</div>
              <template v-if="c.geom">
                <span class="pt-mark hi mono" :style="markStyle(c, c.hiIdx)">최고 {{ fmtNum(c.hi, 2) }}</span>
                <span class="pt-mark lo mono" :style="markStyle(c, c.loIdx)">최저 {{ fmtNum(c.lo, 2) }}</span>
                <span class="last-dot" :class="c.trend" :style="dotStyle(c, c.spark.length - 1)"></span>
              </template>
              <template v-if="idxHover[c.key]">
                <span class="xhair" :style="{ left: idxHover[c.key].x + '%' }"></span>
                <span class="hv-dot" :style="{ left: idxHover[c.key].x + '%', top: idxHover[c.key].y + '%' }"></span>
                <div class="hv-tip" :class="{ flip: idxHover[c.key].x > 58 }" :style="{ left: idxHover[c.key].x + '%' }">
                  <span class="muted">{{ idxHover[c.key].date }}</span>
                  <b class="mono">{{ fmtNum(idxHover[c.key].v, 2) }}</b>
                  <span class="mono" :class="dir(idxHover[c.key].chg)">{{ fmtPct(idxHover[c.key].chg) }}</span>
                </div>
              </template>
            </div>
            <div class="idx-range mono">{{ c.rangeText }}</div>

            <dl class="idx-stats">
              <div><dt>{{ c.days }}일 최고</dt><dd class="mono">{{ fmtNum(c.hi, 2) }}</dd></div>
              <div><dt>{{ c.days }}일 최저</dt><dd class="mono">{{ fmtNum(c.lo, 2) }}</dd></div>
              <div><dt>고점 대비</dt><dd class="mono" :class="dir(c.fromHigh)">{{ fmtPct(c.fromHigh) }}</dd></div>
              <div><dt>기간 수익률</dt><dd class="mono" :class="dir(c.periodRet)">{{ fmtPct(c.periodRet) }}</dd></div>
            </dl>

            <div class="breadth">
              <div class="breadth-hd">
                <span class="sec-label">시장 폭</span>
                <span v-if="c.breadth" class="muted small">시총 상위 {{ c.breadth.sample }}종목 기준</span>
              </div>
              <template v-if="c.breadth">
                <div class="breadth-bar" :title="`상승 ${c.breadth.up} · 보합 ${c.breadth.flat} · 하락 ${c.breadth.down}`">
                  <span class="b-up" :style="{ width: c.breadth.upW + '%' }"></span>
                  <span class="b-flat" :style="{ width: c.breadth.flatW + '%' }"></span>
                  <span class="b-down" :style="{ width: c.breadth.downW + '%' }"></span>
                </div>
                <div class="breadth-legend mono">
                  <span class="up">상승 {{ c.breadth.up }}</span>
                  <span class="muted">보합 {{ c.breadth.flat }}</span>
                  <span class="down">하락 {{ c.breadth.down }}</span>
                </div>
              </template>
              <div v-else-if="mv[c.market].loading" class="sk sk-h8"></div>
              <div v-else class="muted small">{{ mv[c.market].error || '시장 폭 데이터 없음' }}</div>
            </div>
          </div>
        </article>

        <!-- 글로벌 마켓 -->
        <article class="panel global-panel">
          <header class="panel-hd">
            <h2 class="panel-title"><i class="t-bar"></i>글로벌 마켓</h2>
            <span class="hd-right muted small">Yahoo Finance · 1개월 추이</span>
          </header>
          <div v-if="!overview && !ovError" class="gl-body">
            <div v-for="n in 3" :key="n" class="gl-group">
              <div class="sk sk-h10 w20"></div>
              <div class="gl-grid">
                <div v-for="m in 3" :key="m" class="sk sk-cell"></div>
              </div>
            </div>
          </div>
          <div v-else-if="globalError" class="state-box err">
            <span>⚠ {{ globalError }}</span>
            <button class="btn-ghost sm" @click="loadOverview">다시 시도</button>
          </div>
          <div v-else class="gl-body">
            <div v-for="g in globalGroups" :key="g.key" class="gl-group">
              <div class="sec-label">{{ g.label }}</div>
              <div class="gl-grid">
                <div v-for="it in g.items" :key="it.symbol" class="gl-cell" :class="{ bad: it.error }" :title="it.error ? it.name + ' — ' + it.error : it.symbol">
                  <div class="gl-top">
                    <span class="gl-name">{{ it.name }}</span>
                    <span class="gl-pct mono" :class="dir(it.pct)">{{ it.error ? '—' : fmtPct(it.pct) }}</span>
                  </div>
                  <div class="gl-bot">
                    <span class="gl-price mono">{{ it.priceText }}</span>
                    <svg v-if="it.path" class="gl-spark" viewBox="0 0 64 22" preserveAspectRatio="none" aria-hidden="true">
                      <path :d="it.path" fill="none" :stroke="COLORS[it.trend]" stroke-width="1.3" vector-effect="non-scaling-stroke" />
                    </svg>
                    <span v-else class="gl-spark-empty muted">{{ it.error ? '오류' : '' }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </article>
      </section>

      <!-- ═══════════════════ 오늘의 테마 (평일 아침 자동 추출) ═══════════════════ -->
      <ThemePanel @screen="screenTheme" />

      <!-- ═══════════════════ ROW 2 : 주도주 · 암호화폐 ═══════════════════ -->
      <section class="row row-2">
        <article ref="moversEl" class="panel movers-panel">
          <header class="panel-hd">
            <h2 class="panel-title"><i class="t-bar"></i>국내 시장 주도주</h2>
            <div class="seg" role="group" aria-label="시장 선택">
              <button v-for="m in MARKETS" :key="m.key" :class="{ on: moversMarket === m.key }" @click="moversMarket = m.key">{{ m.label }}</button>
            </div>
            <span v-if="curBreadth" class="hd-right mono small">
              <span class="up">▲{{ curBreadth.up }}</span>
              <span class="muted"> · {{ curBreadth.flat }} · </span>
              <span class="down">▼{{ curBreadth.down }}</span>
            </span>
          </header>
          <div class="tabs" role="tablist" aria-label="주도주 기준">
            <button v-for="t in MOVER_TABS" :key="t.key" role="tab" class="tab" :class="{ on: moversTab === t.key }" :aria-selected="moversTab === t.key" @click="moversTab = t.key">{{ t.label }}</button>
          </div>
          <div class="tbl-wrap movers-wrap">
            <div v-if="mv[moversMarket].loading && !moverRows.length" class="sk-rows">
              <div v-for="n in 10" :key="n" class="sk sk-row"></div>
            </div>
            <div v-else-if="!moverRows.length" class="state-box" :class="{ err: moverError }">
              <span>{{ moverError ? '⚠ ' + moverError : '표시할 종목이 없습니다' }}</span>
              <button class="btn-ghost sm" @click="loadMovers(moversMarket)">다시 시도</button>
            </div>
            <table v-else class="tbl movers-tbl">
              <thead>
                <tr>
                  <th class="c w-rank">순위</th>
                  <th class="l">종목명</th>
                  <th class="r">현재가</th>
                  <th class="r">등락률</th>
                  <th v-if="moversTab === 'volume'" class="r">거래량</th>
                  <th class="r">거래대금(억)</th>
                  <th class="r">시총(억)</th>
                  <th class="c w-act"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(r, i) in moverRows" :key="r.ticker" class="click-row" @click="goAnalyze(r)">
                  <td class="c"><span class="rank-num mono" :class="{ top: i < 3 }">{{ i + 1 }}</span></td>
                  <td class="l">
                    <div class="nm-cell">
                      <span class="nm">{{ r.name }}</span>
                      <span class="code mono">{{ r.ticker }}</span>
                    </div>
                  </td>
                  <td class="r mono" :class="dir(r.pct)">{{ fmtNum(r.price) }}</td>
                  <td class="r mono">
                    <span class="pct-pill sm" :class="dir(r.pct)">{{ fmtPct(r.pct) }}</span>
                  </td>
                  <td v-if="moversTab === 'volume'" class="r mono">{{ fmtVolume(r.volume) }}</td>
                  <td class="r mono">{{ fmtNum(milToEok(r.value_mil)) }}</td>
                  <td class="r mono">{{ fmtNum(r.mcap_eok) }}</td>
                  <td class="c"><button class="go-btn sm" @click.stop="goAnalyze(r)">분석</button></td>
                </tr>
              </tbody>
            </table>
          </div>
        </article>

        <!-- 암호화폐 -->
        <article class="panel crypto-panel">
          <header class="panel-hd">
            <h2 class="panel-title"><i class="t-bar amber"></i>암호화폐 · {{ crypto.source || 'CoinMarketCap' }}</h2>
            <span v-if="crypto.fallback" class="tag-warn" :title="crypto.fallback">대체 소스</span>
            <span class="hd-right muted small">USD 기준</span>
          </header>
          <div v-if="crypto.loading" class="cr-body">
            <div class="cr-stats"><div v-for="n in 4" :key="n" class="sk sk-stat"></div></div>
            <div class="sk-rows"><div v-for="n in 8" :key="n" class="sk sk-row"></div></div>
          </div>
          <div v-else-if="crypto.error" class="state-box err">
            <span>⚠ {{ crypto.error }}</span>
            <button class="btn-ghost sm" @click="loadOverview">다시 시도</button>
          </div>
          <div v-else class="cr-body">
            <div class="cr-stats">
              <div class="cr-stat">
                <span class="sec-label">총 시가총액</span>
                <b class="mono">{{ fmtUsdBig(crypto.g.total_mcap) }}</b>
                <span class="mono small" :class="dir(crypto.g.mcap_change_24h)">{{ fmtPct(crypto.g.mcap_change_24h) }} <span class="muted">24h</span></span>
              </div>
              <div class="cr-stat">
                <span class="sec-label">24시간 거래량</span>
                <b class="mono">{{ fmtUsdBig(crypto.g.total_volume24h) }}</b>
                <span class="muted small">전 코인 합계</span>
              </div>
              <div class="cr-stat">
                <span class="sec-label">BTC 도미넌스</span>
                <b class="mono">{{ fmtPlainPct(crypto.g.btc_dominance) }}</b>
                <div class="dom-bar" :title="`BTC ${fmtPlainPct(crypto.g.btc_dominance)} · ETH ${fmtPlainPct(crypto.g.eth_dominance)}`">
                  <span class="dom-btc" :style="{ width: clampPct(crypto.g.btc_dominance) + '%' }"></span>
                  <span class="dom-eth" :style="{ width: clampPct(crypto.g.eth_dominance) + '%' }"></span>
                </div>
                <span class="muted small mono">ETH {{ fmtPlainPct(crypto.g.eth_dominance) }}</span>
              </div>
              <div class="cr-gauge">
                <span class="sec-label">공포 · 탐욕 지수</span>
                <svg viewBox="0 0 200 112" class="gauge-svg" role="img" :aria-label="fearGreed ? `공포 탐욕 지수 ${fearGreed.score}, ${fearGreed.label}` : '공포 탐욕 지수 없음'">
                  <g transform="translate(100,96)">
                    <path v-for="(s, i) in GAUGE_SEGS" :key="i" :d="s.d" :fill="s.color" :opacity="fearGreed && fearGreed.seg === i ? 1 : 0.28" />
                    <template v-if="fearGreed">
                      <line x1="0" y1="0" :x2="fearGreed.nx" :y2="fearGreed.ny" stroke="#e2e8f0" stroke-width="2.4" stroke-linecap="round" />
                      <circle r="5" fill="#0f1419" stroke="#e2e8f0" stroke-width="2" />
                    </template>
                  </g>
                  <text x="14" y="110" class="g-tick">0</text>
                  <text x="186" y="110" class="g-tick" text-anchor="end">100</text>
                </svg>
                <div class="gauge-read">
                  <b class="mono" :style="{ color: fearGreed ? fearGreed.color : '#8892a4' }">{{ fearGreed ? fearGreed.score : '—' }}</b>
                  <span :style="{ color: fearGreed ? fearGreed.color : '#8892a4' }">{{ fearGreed ? fearGreed.label : '데이터 없음' }}</span>
                </div>
              </div>
            </div>
            <div v-if="crypto.gError" class="inline-note">⚠ 글로벌 지표 일부를 불러오지 못했습니다</div>
            <div class="tbl-wrap crypto-wrap">
              <table v-if="crypto.coins.length" class="tbl crypto-tbl">
                <thead>
                  <tr>
                    <th class="c w-rank">순위</th>
                    <th class="l">코인</th>
                    <th class="r">가격(USD)</th>
                    <th class="r">1h</th>
                    <th class="r">24h</th>
                    <th class="r">7d</th>
                    <th class="r">시가총액</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="cn in crypto.coins" :key="cn.symbol + cn.rank">
                    <td class="c mono muted">{{ cn.rank ?? '—' }}</td>
                    <td class="l">
                      <div class="nm-cell">
                        <span class="nm">{{ cn.symbol }}</span>
                        <span class="code">{{ cn.name }}</span>
                      </div>
                    </td>
                    <td class="r mono">{{ fmtUsdPrice(cn.price) }}</td>
                    <td class="r mono" :class="dir(cn.pct_1h)">{{ fmtPct(cn.pct_1h) }}</td>
                    <td class="r mono" :class="dir(cn.pct_24h)">{{ fmtPct(cn.pct_24h) }}</td>
                    <td class="r mono" :class="dir(cn.pct_7d)">{{ fmtPct(cn.pct_7d) }}</td>
                    <td class="r mono">{{ fmtUsdBig(cn.mcap) }}</td>
                  </tr>
                </tbody>
              </table>
              <div v-else class="state-box">코인 시세가 없습니다</div>
            </div>
          </div>
        </article>
      </section>

      <!-- ═══════════════════ ROW 3 : 조건검색 스크리너 ═══════════════════ -->
      <section ref="scrEl" class="panel scr-panel">
        <header class="panel-hd scr-hd">
          <h2 class="panel-title lg"><i class="t-bar green"></i>조건검색 · 종목 스크리너</h2>
          <span class="hd-desc muted">조건을 A·B·C… 문자로 만들고 and / or / not 논리식으로 결합하는 영웅문 조건검색 방식</span>
          <span v-if="scr.result && scr.result.as_of" class="hd-right mono muted small">기준 {{ scr.result.as_of }}</span>
        </header>

        <div v-if="scr.scope" class="scope-bar" role="status">
          <span class="scope-chip">
            <span class="sec-label">검사 범위</span>
            <b>{{ scr.scope.name }}</b> 테마 <span class="mono">{{ scr.scope.codes.length }}</span>종목
          </span>
          <span v-if="scr.scope.note" class="muted small">{{ scr.scope.note }}</span>
          <span v-if="scopeMissing" class="muted small">· 시세 없음 {{ scopeMissing }}종목</span>
          <button class="link-btn scope-x" :disabled="scr.running" @click="clearScope">범위 해제 (시장 전체로) ×</button>
        </div>

        <div class="scr-body">
          <!-- 좌측: 조건 빌더 -->
          <aside class="scr-builder">
            <div v-if="defs.loading" class="blk">
              <div class="sk sk-h10 w30"></div>
              <div v-for="n in 5" :key="n" class="sk sk-row"></div>
            </div>
            <div v-else-if="defs.error" class="state-box err">
              <span>⚠ 조건 정의를 불러오지 못했습니다 · {{ defs.error }}</span>
              <button class="btn-ghost sm" @click="loadDefs">다시 시도</button>
            </div>
            <template v-else>
              <!-- 추천 조건식 -->
              <div class="blk">
                <div class="blk-hd">
                  <span class="sec-label">추천 조건식</span>
                  <span v-if="scr.presetId && scr.presetDirty" class="tag-mod">수정됨</span>
                </div>
                <ul class="preset-list">
                  <li v-for="p in defs.presets" :key="p.id">
                    <button class="preset" :class="{ on: scr.presetId === p.id }" :title="p.desc" @click="loadPreset(p)">
                      <span class="preset-name">{{ p.name }}</span>
                      <span class="preset-desc">{{ p.desc }}</span>
                      <span class="preset-expr mono">{{ p.expr }}</span>
                    </button>
                  </li>
                </ul>
              </div>

              <!-- 검색 대상 -->
              <div class="blk blk-row" :class="{ scoped: scr.scope }" :title="scr.scope ? '테마 범위 검사 중에는 시장·검색 대상 대신 테마 구성 종목만 검사합니다' : null">
                <label class="fld">
                  <span class="sec-label">시장</span>
                  <select v-model="scr.market" class="sel" :disabled="!!scr.scope">
                    <option value="ALL">전체</option>
                    <option value="KOSPI">코스피</option>
                    <option value="KOSDAQ">코스닥</option>
                  </select>
                </label>
                <label class="fld">
                  <span class="sec-label">검색 대상</span>
                  <select v-model.number="scr.size" class="sel" :disabled="!!scr.scope">
                    <option v-for="n in UNIVERSE_SIZES" :key="n" :value="n">시총 상위 {{ n }}종목</option>
                  </select>
                </label>
              </div>

              <!-- 조건 카탈로그 -->
              <div class="blk">
                <div class="blk-hd">
                  <span class="sec-label">조건 추가</span>
                  <span class="muted small mono">{{ scr.conds.length }}/{{ MAX_CONDS }}</span>
                </div>
                <div v-for="g in catalog" :key="g.group" class="cat-group">
                  <div class="cat-title">{{ g.group }}</div>
                  <div class="cat-items">
                    <button v-for="d in g.items" :key="d.type" class="cat-item" :disabled="scr.conds.length >= MAX_CONDS" :title="d.desc" @click="addCond(d.type)">
                      <span class="plus">+</span>{{ d.label }}
                    </button>
                  </div>
                </div>
              </div>

              <!-- 선택된 조건 -->
              <div class="blk">
                <div class="blk-hd">
                  <span class="sec-label">조건 목록</span>
                  <button v-if="scr.conds.length" class="link-btn" @click="clearConds">전체 삭제</button>
                </div>
                <div v-if="!scr.conds.length" class="empty-mini">추천 조건식을 고르거나 위 카탈로그에서 조건을 추가하세요</div>
                <transition-group name="cond" tag="ul" class="cond-list">
                  <li v-for="c in scr.conds" :key="c.letter" class="cond-row">
                    <span class="letter mono">{{ c.letter }}</span>
                    <div class="cond-main">
                      <div class="cond-top">
                        <span class="cond-label">{{ condDef(c.type) ? condDef(c.type).label : c.type }}</span>
                        <span v-if="condDef(c.type) && condDef(c.type).unit" class="cond-unit">{{ condDef(c.type).unit }}</span>
                        <span class="stage" :class="'s' + stageOf(c.type)">{{ stageLabel(stageOf(c.type)) }}</span>
                      </div>
                      <div v-if="Object.keys(c.params).length" class="cond-params">
                        <label v-for="(v, k) in c.params" :key="k" class="param">
                          <span>{{ paramLabel(k) }}</span>
                          <input class="num-in mono" type="number" step="any" inputmode="decimal" :value="v ?? ''" placeholder="제한 없음" @input="setParam(c, k, $event)" />
                        </label>
                      </div>
                      <div v-if="condDef(c.type) && condDef(c.type).desc" class="cond-desc">{{ condDef(c.type).desc }}</div>
                    </div>
                    <button class="x-btn" :aria-label="`조건 ${c.letter} 삭제`" title="삭제" @click="removeCond(c.letter)">×</button>
                  </li>
                </transition-group>
              </div>

              <!-- 조건식 -->
              <div class="blk">
                <div class="blk-hd">
                  <span class="sec-label">조건식</span>
                  <button v-if="scr.exprMode !== 'auto'" class="link-btn" @click="resetExpr">자동 생성으로 되돌리기</button>
                </div>
                <input
                  ref="exprInput"
                  class="expr-in mono"
                  :class="{ bad: scr.conds.length && exprError }"
                  :value="scr.expr"
                  spellcheck="false"
                  autocomplete="off"
                  placeholder="예: A and (B or C)"
                  aria-label="조건식"
                  @input="onExprInput"
                  @click="saveCaret"
                  @keyup="saveCaret"
                  @select="saveCaret"
                  @keydown.enter.prevent="runScreener"
                />
                <div class="tok-chips">
                  <button v-for="t in EXPR_TOKENS" :key="t" class="tok mono" @click="insertToken(t)">{{ t }}</button>
                  <span v-if="scr.conds.length" class="tok-sep"></span>
                  <button v-for="c in scr.conds" :key="'l' + c.letter" class="tok tok-l mono" @click="insertToken(c.letter)">{{ c.letter }}</button>
                </div>
                <div class="expr-msg" :class="!scr.conds.length ? 'idle' : exprError ? 'err' : unusedLetters.length ? 'warn' : 'ok'">
                  <template v-if="!scr.conds.length">조건을 하나 이상 추가하세요</template>
                  <template v-else-if="exprError">⚠ {{ exprError }}</template>
                  <template v-else-if="unusedLetters.length">식에 쓰이지 않은 조건: {{ unusedLetters.join(', ') }} (신호 표시만)</template>
                  <template v-else>✓ 논리식 유효 · 조건 {{ scr.conds.length }}개</template>
                </div>
              </div>

              <button class="run-btn" :disabled="!canRun" @click="runScreener">
                <svg v-if="!scr.running" viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true">
                  <circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.2" y2="16.2" />
                </svg>
                <span v-else class="btn-spinner"></span>
                {{ scr.running ? '검색 중…' : '검색 실행' }}
              </button>
            </template>
          </aside>

          <!-- 우측: 결과 -->
          <div class="scr-results">
            <!-- 스캔 중 -->
            <div v-if="scr.running" class="scan">
              <div class="scan-top">
                <div class="radar" aria-hidden="true">
                  <div class="radar-sweep"></div>
                  <i class="blip b1"></i><i class="blip b2"></i><i class="blip b3"></i><i class="blip b4"></i>
                  <div class="radar-core"></div>
                </div>
                <div class="scan-info">
                  <div class="scan-title">조건검색 실행 중<span class="dots"><i></i><i></i><i></i></span></div>
                  <div class="scan-sub">
                    <template v-if="scr.scope">{{ scr.scope.name }} 테마 {{ scr.scope.codes.length }}종목 · 조건 {{ scr.conds.length }}개</template>
                    <template v-else>{{ marketLabel(scr.market) }} · 시총 상위 {{ scr.size }}종목 · 조건 {{ scr.conds.length }}개</template>
                    <span class="scan-expr mono">{{ scr.expr }}</span>
                  </div>
                  <Tick :interval="250" v-slot="{ now }">
                    <ol class="scan-phases">
                      <li v-for="(ph, i) in scanPhases" :key="i" :class="phaseState(i, now, scr.startedAt, scanPhases.length, 14)">
                        <span class="ph-mark"></span>{{ ph }}
                      </li>
                    </ol>
                    <div class="prog"><span class="prog-fill" :style="{ width: fakeProgress(now, scr.startedAt, 14) + '%' }"></span></div>
                    <div class="scan-elapsed mono">경과 {{ elapsedSec(now, scr.startedAt) }}초 · 보통 10~40초 소요</div>
                  </Tick>
                </div>
              </div>
              <div class="scan-rows" aria-hidden="true">
                <div v-for="n in 6" :key="n" class="scan-row"><span class="sk sk-row"></span></div>
                <div class="scan-beam"></div>
              </div>
            </div>

            <!-- 오류 -->
            <div v-else-if="scr.error" class="state-box err big">
              <span>⚠ 검색 실패 · {{ scr.error }}</span>
              <button class="btn-ghost sm" :disabled="!canRun" @click="runScreener">다시 시도</button>
            </div>

            <!-- 대기 -->
            <div v-else-if="!scr.result" class="scr-idle">
              <svg viewBox="0 0 64 64" width="56" height="56" aria-hidden="true" fill="none" stroke="#3b82f6" stroke-width="2">
                <rect x="6" y="10" width="40" height="44" rx="5" stroke="#2a3041" />
                <line x1="14" y1="22" x2="36" y2="22" stroke="#2a3041" />
                <line x1="14" y1="31" x2="32" y2="31" stroke="#2a3041" />
                <line x1="14" y1="40" x2="28" y2="40" stroke="#2a3041" />
                <circle cx="42" cy="40" r="10" /><line x1="49.5" y1="47.5" x2="58" y2="56" stroke-linecap="round" />
              </svg>
              <h3>조건을 설정하고 검색을 실행하세요</h3>
              <ol class="idle-steps">
                <li><b>1</b>추천 조건식을 고르거나 카탈로그에서 조건을 추가합니다</li>
                <li><b>2</b>파라미터와 논리식(and · or · not · 괄호)을 조정합니다</li>
                <li><b>3</b>점수순 결과에서 <span class="accent">5단계 AI 분석</span>으로 바로 넘어갑니다</li>
              </ol>
              <p class="muted small">1차로 시세 조건(시총·가격·등락률·거래대금)으로 좁힌 뒤, 후보 종목의 일봉 130개로 기술적 지표를 계산합니다.</p>
            </div>

            <!-- 결과 -->
            <template v-else>
              <div class="funnel">
                <div class="fn-step">
                  <span class="fn-num mono">{{ fmtNum(scr.result.universe) }}</span>
                  <span class="fn-lbl">유니버스</span>
                </div>
                <span class="fn-arrow">›</span>
                <div class="fn-step">
                  <span class="fn-num mono">{{ fmtNum(scr.result.candidates) }}</span>
                  <span class="fn-lbl">1차 후보</span>
                </div>
                <span class="fn-arrow">›</span>
                <div class="fn-step hit">
                  <span class="fn-num mono">{{ fmtNum(scr.result.matched) }}</span>
                  <span class="fn-lbl">최종 포착</span>
                </div>
                <div class="fn-bars" aria-hidden="true">
                  <span class="fb fb-u"></span>
                  <span class="fb fb-c" :style="{ width: funnelW(scr.result.candidates) + '%' }"></span>
                  <span class="fb fb-m" :style="{ width: funnelW(scr.result.matched) + '%' }"></span>
                </div>
                <div class="fn-meta mono">
                  <span>{{ scr.result.elapsed_sec ?? '—' }}초</span>
                  <span class="fn-expr">{{ scr.result.expr }}</span>
                </div>
              </div>
              <div v-if="!scrRows.length" class="state-box">
                <span>조건식을 모두 만족하는 종목이 없습니다. 조건을 완화하거나 or 로 묶어 보세요.</span>
              </div>
              <div v-else class="tbl-wrap scr-wrap">
                <table class="tbl scr-tbl">
                  <thead>
                    <tr>
                      <th class="c w-rank">#</th>
                      <th class="l"><button class="th-btn" @click="sortBy('score')">점수 <i>{{ sortMark('score') }}</i></button></th>
                      <th class="l">종목</th>
                      <th class="r">현재가</th>
                      <th class="r"><button class="th-btn" @click="sortBy('pct')">등락률 <i>{{ sortMark('pct') }}</i></button></th>
                      <th class="r"><button class="th-btn" @click="sortBy('rsi')">RSI <i>{{ sortMark('rsi') }}</i></button></th>
                      <th class="r"><button class="th-btn" @click="sortBy('ret20')">20일 수익률 <i>{{ sortMark('ret20') }}</i></button></th>
                      <th v-if="scrHasFund" class="r"><button class="th-btn" @click="sortBy('per')">PER <i>{{ sortMark('per') }}</i></button></th>
                      <th v-if="scrHasFund" class="r"><button class="th-btn" @click="sortBy('pbr')">PBR <i>{{ sortMark('pbr') }}</i></button></th>
                      <th class="c">40일 추이</th>
                      <th class="l">충족 신호</th>
                      <th class="c w-act2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(r, i) in scrRows" :key="r.ticker">
                      <td class="c mono muted">{{ i + 1 }}</td>
                      <td class="l">
                        <div class="score-cell">
                          <div class="score-bar"><span :style="{ width: r.scoreW + '%' }"></span></div>
                          <span class="mono score-num">{{ r.score ?? '—' }}</span>
                        </div>
                      </td>
                      <td class="l">
                        <div class="nm-cell link" @click="goAnalyze(r)">
                          <span class="nm">{{ r.name }}</span>
                          <span class="code mono">{{ r.ticker }} · {{ mktShort(r.market) }}</span>
                        </div>
                      </td>
                      <td class="r mono" :class="dir(r.pct)">{{ fmtNum(r.price) }}</td>
                      <td class="r"><span class="pct-pill sm mono" :class="dir(r.pct)">{{ fmtPct(r.pct) }}</span></td>
                      <td class="r mono" :class="rsiClass(r.rsi)">{{ fmtNum(r.rsi, 1) }}</td>
                      <td class="r mono" :class="dir(r.ret20)">{{ fmtPct(r.ret20) }}</td>
                      <td v-if="scrHasFund" class="r mono">{{ fmtNum(r.per, 1) }}</td>
                      <td v-if="scrHasFund" class="r mono">{{ fmtNum(r.pbr, 2) }}</td>
                      <td class="c">
                        <svg v-if="r.sparkD" class="mini-spark" viewBox="0 0 90 26" preserveAspectRatio="none" aria-hidden="true">
                          <path :d="r.sparkD" fill="none" :stroke="COLORS[r.trend]" stroke-width="1.3" vector-effect="non-scaling-stroke" />
                        </svg>
                        <span v-else class="muted">—</span>
                      </td>
                      <td class="l">
                        <div class="sigs">
                          <span v-for="s in r.signals" :key="s.letter" class="sig" :class="{ ok: s.ok }" :title="sigTip(s)">
                            <b>{{ s.letter }}</b>{{ s.label }}
                          </span>
                        </div>
                      </td>
                      <td class="c"><button class="go-btn" @click="goAnalyze(r)">5단계 분석 →</button></td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
          </div>
        </div>
      </section>

      <!-- ═══════════════════ ROW 4 : AI 추천 종목 ═══════════════════ -->
      <section class="panel rec-panel">
        <header class="panel-hd">
          <h2 class="panel-title lg"><i class="t-bar green"></i>AI 추천 종목</h2>
          <span class="hd-desc muted">여러 조건검색 전략을 동시에 돌려 2개 이상 전략에 겹쳐 포착된 종목을 우선합니다</span>
          <div class="hd-right hd-tools">
            <span v-if="rec.data && rec.data.as_of" class="mono muted small">기준 {{ rec.data.as_of }}</span>
            <button class="icon-btn" :disabled="rec.loading" title="추천 다시 불러오기" aria-label="추천 다시 불러오기" @click="loadRecommend">
              <svg viewBox="0 0 24 24" width="14" height="14" :class="{ spin: rec.loading }" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                <polyline points="23 4 23 10 17 10" />
                <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
              </svg>
            </button>
          </div>
        </header>

        <div v-if="recPresets.length && !rec.loading" class="rec-presets">
          <span class="sec-label">전략별 포착</span>
          <span v-for="p in recPresets" :key="p.id" class="rp-chip" :class="{ err: p.error }" :title="p.error || ''">
            <span>{{ p.name }}</span><b class="mono">{{ p.error ? '오류' : fmtNum(p.matched) }}</b>
          </span>
        </div>

        <div v-if="rec.loading" class="rec-loading">
          <div class="rec-loader">
            <div class="eq" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></div>
            <div class="rec-loader-text">
              <b>AI가 전 종목을 전략별로 스캔하고 있습니다</b>
              <Tick :interval="500" v-slot="{ now }">
                <span class="muted small">
                  {{ recPhaseText(now) }} · 경과 <span class="mono">{{ elapsedSec(now, rec.startedAt, 0) }}</span>초 · 최초 1회 30~90초 소요 (이후 캐시)
                </span>
                <div class="prog thin"><span class="prog-fill green" :style="{ width: fakeProgress(now, rec.startedAt, 40) + '%' }"></span></div>
              </Tick>
            </div>
          </div>
          <div class="rec-grid">
            <div v-for="n in 8" :key="n" class="rec-card sk-card">
              <div class="sk sk-h14 w60"></div>
              <div class="sk sk-h10 w35"></div>
              <div class="sk sk-h24 w45"></div>
              <div class="sk sk-spark"></div>
              <div class="sk sk-h10 w80"></div>
              <div class="sk sk-h10 w70"></div>
            </div>
          </div>
        </div>
        <div v-else-if="rec.error" class="state-box err">
          <span>⚠ 추천 종목을 불러오지 못했습니다 · {{ rec.error }}</span>
          <button class="btn-ghost sm" @click="loadRecommend">다시 시도</button>
        </div>
        <div v-else-if="!rec.data" class="state-box">
          <span class="muted">다른 패널을 먼저 불러온 뒤 AI 추천 스캔을 시작합니다…</span>
        </div>
        <div v-else-if="!recItems.length" class="state-box">
          <span>현재 여러 전략에 동시에 포착된 종목이 없습니다.</span>
        </div>
        <div v-else class="rec-grid">
          <article v-for="it in recItems" :key="it.ticker" class="rec-card">
            <div class="rc-top">
              <span class="rc-rank mono">{{ String(it.rank).padStart(2, '0') }}</span>
              <div class="rc-name">
                <b class="link" @click="goAnalyze(it)">{{ it.name }}</b>
                <span class="mono muted">{{ it.ticker }} · {{ mktShort(it.market) }}</span>
              </div>
              <div class="rc-score" :title="'종합 점수 ' + it.score">
                <span class="mono">{{ it.score ?? '—' }}</span>
                <small>점수</small>
              </div>
            </div>
            <div class="rc-quote">
              <span class="rc-price mono" :class="dir(it.pct)">{{ fmtNum(it.price) }}</span>
              <span class="pct-pill sm mono" :class="dir(it.pct)">{{ fmtPct(it.pct) }}</span>
            </div>
            <svg v-if="it.line" class="rc-spark" viewBox="0 0 240 48" preserveAspectRatio="none" aria-hidden="true">
              <path :d="it.area" :fill="`url(#wad-grad-${it.trend})`" />
              <path :d="it.line" fill="none" :stroke="COLORS[it.trend]" stroke-width="1.5" vector-effect="non-scaling-stroke" />
            </svg>
            <div v-else class="rc-spark empty muted small">추이 데이터 없음</div>
            <div class="rc-metrics">
              <span>RSI <b class="mono" :class="rsiClass(it.rsi)">{{ fmtNum(it.rsi, 1) }}</b></span>
              <span>20일 <b class="mono" :class="dir(it.ret20)">{{ fmtPct(it.ret20) }}</b></span>
              <span>시총 <b class="mono">{{ fmtEokShort(it.mcap_eok) }}</b></span>
            </div>
            <div v-if="it.presets.length" class="rc-badges">
              <span v-for="p in it.presets" :key="p" class="badge-blue">{{ p }}</span>
            </div>
            <div v-if="it.tags.length" class="rc-tags">
              <span v-for="t in it.tags" :key="t" class="tag-green">{{ t }}</span>
            </div>
            <p v-if="it.reason" class="rc-reason">{{ it.reason }}</p>
            <button class="go-btn full" @click="goAnalyze(it)">5단계 분석 →</button>
          </article>
        </div>
      </section>

      <!-- ═══════════════════ ROW 5 : 뉴스 · 일정 ═══════════════════ -->
      <section class="row row-5">
        <article class="panel news-panel">
          <header class="panel-hd">
            <h2 class="panel-title"><i class="t-bar"></i>뉴스</h2>
            <span v-if="news[newsTab].source" class="hd-right muted small">{{ news[newsTab].source }}</span>
          </header>
          <div class="tabs" role="tablist" aria-label="뉴스 분류">
            <button v-for="t in NEWS_TABS" :key="t.key" role="tab" class="tab" :class="{ on: newsTab === t.key }" :aria-selected="newsTab === t.key" @click="selectNews(t.key)">{{ t.label }}</button>
          </div>
          <div class="news-body">
            <div v-if="news[newsTab].loading && !news[newsTab].items.length" class="sk-rows">
              <div v-for="n in 8" :key="n" class="sk sk-row"></div>
            </div>
            <div v-else-if="!news[newsTab].items.length" class="state-box" :class="{ err: news[newsTab].error }">
              <span>{{ news[newsTab].error ? '⚠ ' + news[newsTab].error : '뉴스가 없습니다' }}</span>
              <button class="btn-ghost sm" @click="loadNews(newsTab, true)">다시 시도</button>
            </div>
            <ol v-else class="news-list">
              <li v-for="(n, i) in news[newsTab].items" :key="(n.url || '') + i" class="news-item">
                <span class="news-idx mono">{{ String(i + 1).padStart(2, '0') }}</span>
                <div class="news-main">
                  <a :href="n.url" target="_blank" rel="noopener noreferrer" class="news-title">{{ n.title }}</a>
                  <span class="news-meta">
                    <span class="press">{{ n.press || '언론사 미상' }}</span>
                    <span v-if="n.time" class="mono">{{ n.time }}</span>
                  </span>
                </div>
              </li>
            </ol>
          </div>
        </article>

        <article class="panel cal-panel">
          <header class="panel-hd">
            <h2 class="panel-title"><i class="t-bar amber"></i>주요 증시 일정</h2>
            <span class="hd-right muted small">향후 45일</span>
          </header>
          <div class="cal-body">
            <div v-if="cal.loading && !cal.events.length" class="sk-rows">
              <div v-for="n in 7" :key="n" class="sk sk-row"></div>
            </div>
            <div v-else-if="cal.error" class="state-box err">
              <span>⚠ {{ cal.error }}</span>
              <button class="btn-ghost sm" @click="loadCalendar">다시 시도</button>
            </div>
            <div v-else-if="!calGroups.length" class="state-box">예정된 일정이 없습니다</div>
            <div v-else class="cal-list">
              <div v-for="g in calGroups" :key="g.date" class="cal-day" :class="{ today: g.dday === 0 }">
                <div class="cal-date">
                  <span class="cd-md mono">{{ fmtMD(g.date) }}</span>
                  <span class="cd-wd">{{ g.weekday }}</span>
                  <span class="dday mono" :class="{ now: g.dday === 0, soon: g.dday > 0 && g.dday <= 3 }">{{ ddayText(g.dday) }}</span>
                </div>
                <ul class="cal-events">
                  <li v-for="(e, i) in g.items" :key="i" class="cal-ev">
                    <span class="flag" :title="e.region === 'US' ? '미국' : e.region === 'KR' ? '한국' : e.region">{{ flagOf(e.region) }}</span>
                    <span class="stars" :title="'중요도 ' + impOf(e.importance)"><span class="on">{{ '★'.repeat(impOf(e.importance)) }}</span><span class="off">{{ '★'.repeat(3 - impOf(e.importance)) }}</span></span>
                    <span class="ev-title">{{ e.title }}</span>
                    <span v-if="e.category" class="cat-chip" :class="catClass(e.category)">{{ e.category }}</span>
                  </li>
                </ul>
              </div>
            </div>
            <p v-if="cal.note" class="cal-note">※ {{ cal.note }}</p>
          </div>
        </article>
      </section>
    </main>

    <!-- ═══════════════════ 푸터 ═══════════════════ -->
    <footer class="foot">
      <div class="foot-src">
        <span class="sec-label">데이터 출처</span>
        <span>네이버 증권 · Yahoo Finance · CoinMarketCap · Google 뉴스</span>
      </div>
      <p class="foot-disc">
        본 화면의 시세·지표·조건검색 결과와 AI 추천 종목은 정보 제공을 위한 참고 자료이며 투자 권유가 아닙니다.
        해외 시세는 지연될 수 있고, 추천 결과는 과거 데이터에 기반한 기계적 신호입니다.
        투자 판단과 그에 따른 손익의 책임은 투자자 본인에게 있습니다.
      </p>
      <div class="foot-brand mono">WeAidStock · AI 주식 분석 터미널</div>
    </footer>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, defineComponent, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import * as d3 from 'd3'
import api from '../api/index'
import ThemePanel from '../components/ThemePanel.vue'

const router = useRouter()

/* ─────────────────────────────────────────────
 * 로컬 틱 컴포넌트: 시계·경과시간을 부모 재렌더 없이 갱신
 * (슬롯 내용은 이 컴포넌트의 렌더에서만 다시 그려짐)
 * ───────────────────────────────────────────── */
const Tick = defineComponent({
  name: 'Tick',
  props: { interval: { type: Number, default: 1000 } },
  setup (props, { slots }) {
    const now = ref(Date.now())
    let id = null
    onMounted(() => { id = setInterval(() => { now.value = Date.now() }, props.interval) })
    onBeforeUnmount(() => { if (id) clearInterval(id) })
    return () => (slots.default ? slots.default({ now: now.value }) : null)
  }
})

/* ───────────────────────── 상수 ───────────────────────── */
const COLORS = { up: '#ef4444', down: '#3b82f6', flat: '#8892a4' }
const IDX_W = 300
const IDX_H = 92
const IDX_DAYS = 60 // 지수 카드: 최근 60거래일 (고가·저가·기간 수익률 기준)
const KNAME = { kospi: '코스피', kosdaq: '코스닥', kospi200: '코스피200' }
const MARKETS = [{ key: 'KOSPI', label: '코스피' }, { key: 'KOSDAQ', label: '코스닥' }]
const MOVER_TABS = [
  { key: 'up', label: '상승률' },
  { key: 'down', label: '하락률' },
  { key: 'value', label: '거래대금' },
  { key: 'volume', label: '거래량' },
  { key: 'mcap', label: '시가총액' }
]
const NEWS_TABS = [
  { key: 'domestic', label: '국내 증시' },
  { key: 'global', label: '해외 증시' },
  { key: 'semis', label: '반도체' },
  { key: 'crypto', label: '코인' }
]
const GLOBAL_GROUPS = [
  { key: 'index', label: '지수', kinds: ['index'] },
  { key: 'fxrate', label: '환율 · 금리', kinds: ['fx', 'rate'] },
  { key: 'cmdty', label: '원자재 · 선물', kinds: ['cmdty', 'future'] }
]
const GROUP_ORDER = ['시세', '거래량', '이동평균', '보조지표', '가격패턴', '재무']
const PARAM_KO = {
  min: '최소', max: '최대', ratio: '배수', avg: '평균일', short: '단기', mid: '중기', long: '장기',
  within: 'N일 이내', ma: '이평', gap: '허용%', level: '기준', period: '기간', k: '표준편차',
  days: '기간(일)', count: '일수'
}
const EXPR_TOKENS = ['and', 'or', 'not', '(', ')']
const LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'.split('')
const MAX_CONDS = 12
const UNIVERSE_SIZES = [100, 200, 300]

/* ───────────────────────── 포맷 헬퍼 ───────────────────────── */
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
  if (n == null) return '—'
  return n.toLocaleString('ko-KR', { minimumFractionDigits: d, maximumFractionDigits: d })
}
function fmtAbs (v, d = 0) {
  const n = toNum(v)
  return n == null ? '—' : fmtNum(Math.abs(n), d)
}
function fmtPct (v, d = 2) {
  const n = toNum(v)
  if (n == null) return '—'
  if (Math.abs(n) < 0.5 * Math.pow(10, -d)) return (0).toFixed(d) + '%'
  return (n > 0 ? '+' : '') + n.toFixed(d) + '%'
}
function fmtPlainPct (v, d = 2) {
  const n = toNum(v)
  return n == null ? '—' : n.toFixed(d) + '%'
}
function clampPct (v) {
  const n = toNum(v)
  return n == null ? 0 : Math.max(0, Math.min(100, n))
}
function dir (v) {
  const n = toNum(v)
  if (n == null || n === 0) return 'flat'
  return n > 0 ? 'up' : 'down'
}
function arrow (v) {
  const n = toNum(v)
  if (n == null || n === 0) return ''
  return n > 0 ? '▲' : '▼'
}
function fmtUsdBig (v) {
  const n = toNum(v)
  if (n == null) return '—'
  const a = Math.abs(n)
  if (a >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T'
  if (a >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B'
  if (a >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M'
  return '$' + fmtNum(n, 0)
}
function fmtUsdPrice (v) {
  const n = toNum(v)
  if (n == null) return '—'
  const a = Math.abs(n)
  const d = a >= 1 ? 2 : a >= 0.01 ? 4 : 6
  return '$' + n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })
}
function milToEok (v) {
  const n = toNum(v)
  return n == null ? null : Math.round(n / 100) // 백만원 → 억원
}
function fmtEokShort (v) {
  const n = toNum(v)
  if (n == null) return '—'
  if (Math.abs(n) >= 10000) return (n / 10000).toFixed(Math.abs(n) >= 100000 ? 0 : 1) + '조'
  return fmtNum(Math.round(n)) + '억'
}
function fmtVolume (v) {
  const n = toNum(v)
  if (n == null) return '—'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '억'
  if (n >= 1e4) return fmtNum(n / 1e4, 0) + '만'
  return fmtNum(n)
}
function fmtGlobalPrice (it) {
  const n = toNum(it && it.price)
  if (n == null) return '—'
  const d = Math.abs(n) >= 10 ? 2 : 3
  const s = fmtNum(n, d)
  if (it.kind === 'rate' && /TNX/.test(it.symbol || '')) return s + '%'
  return s
}
function fmtDate8 (s) {
  const t = String(s || '')
  return /^\d{8}$/.test(t) ? `${t.slice(0, 4)}.${t.slice(4, 6)}.${t.slice(6, 8)}` : t
}
function fmtMD (s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ''))
  return m ? `${m[2]}.${m[3]}` : String(s || '')
}
function mktShort (m) {
  const t = String(m || '').toUpperCase()
  return t === 'KOSPI' ? '코스피' : t === 'KOSDAQ' ? '코스닥' : (m || '')
}
function marketLabel (m) {
  return m === 'KOSPI' ? '코스피' : m === 'KOSDAQ' ? '코스닥' : '코스피·코스닥 전체'
}
function statusKo (s) {
  const map = { OPEN: '장중', CLOSE: '장 마감', PREOPEN: '장 시작 전', PRE_OPEN: '장 시작 전', HALT: '거래 정지' }
  if (!s) return ''
  return map[String(s).toUpperCase()] || String(s)
}
function rsiClass (v) {
  const n = toNum(v)
  if (n == null) return 'muted'
  if (n >= 70) return 'up'
  if (n <= 30) return 'down'
  return ''
}
function errMsg (e) {
  if (!e) return '알 수 없는 오류'
  if (typeof e === 'string') return e
  const r = e.response && e.response.data
  if (r && typeof r === 'object' && r.error) return String(r.error)
  if (e.error) return String(e.error)
  if (e.code === 'ECONNABORTED') return '응답 시간이 초과되었습니다'
  return e.message ? String(e.message) : '요청 실패'
}

/* ───────────────────────── KST 시계 ───────────────────────── */
let KST_FMT = null
try {
  KST_FMT = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false
  })
} catch (e) { KST_FMT = null }
function kstParts (ts) {
  const o = {}
  if (KST_FMT) {
    for (const p of KST_FMT.formatToParts(new Date(ts))) o[p.type] = p.value
  } else {
    const d = new Date(ts + (new Date(ts).getTimezoneOffset() + 540) * 60000)
    const pad = x => String(x).padStart(2, '0')
    Object.assign(o, {
      year: String(d.getFullYear()), month: pad(d.getMonth() + 1), day: pad(d.getDate()),
      weekday: '일월화수목금토'[d.getDay()], hour: pad(d.getHours()), minute: pad(d.getMinutes()), second: pad(d.getSeconds())
    })
  }
  if (o.hour === '24') o.hour = '00'
  return o
}
function kstDate (ts) {
  const p = kstParts(ts)
  return `${p.year}.${p.month}.${p.day} (${p.weekday})`
}
function kstTime (ts) {
  const p = kstParts(ts)
  return `${p.hour}:${p.minute}:${p.second}`
}
function localMarketStatus () {
  const p = kstParts(Date.now())
  if (p.weekday === '토' || p.weekday === '일') return '휴장'
  const t = Number(p.hour) * 60 + Number(p.minute)
  if (t >= 510 && t < 540) return '장 시작 전 동시호가'
  if (t >= 540 && t < 920) return '장중'
  if (t >= 920 && t < 930) return '장 마감 동시호가'
  if (t >= 940 && t < 1080) return '시간외 거래'
  return '장 마감'
}

/* ───────────────────────── d3 스파크라인 헬퍼 (경로 문자열만 반환) ───────────────────────── */
function sparkGeom (values, w, h, padX = 0, padY = 2) {
  const v = (Array.isArray(values) ? values : []).map(toNum).filter(n => n != null)
  if (v.length < 2) return null
  const x = d3.scaleLinear().domain([0, v.length - 1]).range([padX, w - padX])
  let [mn, mx] = d3.extent(v)
  if (mn === mx) { mn -= 1; mx += 1 }
  const y = d3.scaleLinear().domain([mn, mx]).range([h - padY, padY])
  const line = d3.line().x((d, i) => x(i)).y(d => y(d)).curve(d3.curveMonotoneX)(v) || ''
  const area = d3.area().x((d, i) => x(i)).y0(h).y1(d => y(d)).curve(d3.curveMonotoneX)(v) || ''
  return { line, area, pts: v.map((d, i) => [x(i), y(d)]), y, min: mn, max: mx, values: v }
}
function sparkLine (values, w, h, padY = 2) {
  const g = sparkGeom(values, w, h, 0, padY)
  return g ? g.line : ''
}
function trendOf (values) {
  const v = (Array.isArray(values) ? values : []).map(toNum).filter(n => n != null)
  return v.length < 2 ? 'flat' : dir(v[v.length - 1] - v[0])
}

/* 공포·탐욕 게이지 (d3.arc, 반원) */
const GAUGE_COLORS = ['#3b82f6', '#60a5fa', '#f59e0b', '#f97316', '#ef4444']
const GAUGE_SEGS = (() => {
  const arc = d3.arc().innerRadius(60).outerRadius(80).cornerRadius(2)
  const step = Math.PI / 5
  return GAUGE_COLORS.map((color, i) => ({
    color,
    d: arc({ startAngle: -Math.PI / 2 + i * step + 0.02, endAngle: -Math.PI / 2 + (i + 1) * step - 0.02 }) || ''
  }))
})()
function fgLabel (label, score) {
  const l = String(label || '').toLowerCase()
  if (l) {
    if (l.includes('extreme') && l.includes('fear')) return '극단적 공포'
    if (l.includes('extreme') && l.includes('greed')) return '극단적 탐욕'
    if (l.includes('fear')) return '공포'
    if (l.includes('greed')) return '탐욕'
    if (l.includes('neutral')) return '중립'
  }
  if (score < 25) return '극단적 공포'
  if (score < 45) return '공포'
  if (score < 56) return '중립'
  if (score < 76) return '탐욕'
  return '극단적 탐욕'
}

/* ───────────────────────── 상태: 개요 ───────────────────────── */
const overview = ref(null)
const ovLoading = ref(false)
const ovError = ref('')
const refreshing = ref(false)

async function loadOverview () {
  if (ovLoading.value) return
  ovLoading.value = true
  try {
    const r = await api.get('/api/market/overview', { timeout: 60000 })
    const d = r && r.data
    if (d && typeof d === 'object') {
      overview.value = d
      ovError.value = ''
    } else {
      throw new Error('응답 형식이 올바르지 않습니다')
    }
  } catch (e) {
    ovError.value = errMsg(e)
  } finally {
    ovLoading.value = false
  }
}

const updatedAt = computed(() => (overview.value && overview.value.updated_at) || '')
const marketStatus = computed(() => {
  const m = overview.value && overview.value.market
  return (m && m.status) || localMarketStatus()
})
const statusTone = computed(() => {
  const s = marketStatus.value
  if (s === '장중') return 'live'
  if (/동시호가|시간외|시작 전/.test(s)) return 'warn'
  return 'off'
})

/* 티커 테이프 */
const tapeItems = computed(() => {
  const o = overview.value
  if (!o) return []
  const out = []
  const k = o.korea && !o.korea.error ? o.korea : {}
  for (const key of ['kospi', 'kosdaq', 'kospi200']) {
    const d = k[key]
    if (d && toNum(d.price) != null) out.push({ id: key, name: d.name || KNAME[key], price: fmtNum(d.price, 2), pct: d.pct })
  }
  for (const g of (Array.isArray(o.global) ? o.global : [])) {
    if (g && !g.error && toNum(g.price) != null) out.push({ id: 'g-' + g.symbol, name: g.name || g.symbol, price: fmtGlobalPrice(g), pct: g.pct })
  }
  const coins = o.crypto && Array.isArray(o.crypto.coins) ? o.crypto.coins.slice(0, 5) : []
  for (const c of coins) {
    if (c && toNum(c.price) != null) out.push({ id: 'c-' + c.symbol, name: c.symbol, price: fmtUsdPrice(c.price), pct: c.pct_24h })
  }
  return out
})
const tapeDuration = computed(() => Math.max(40, tapeItems.value.length * 4.2) + 's')

/* ───────────────────────── 상태: 주도주 ───────────────────────── */
const mv = reactive({
  KOSPI: { data: null, loading: false, error: '' },
  KOSDAQ: { data: null, loading: false, error: '' }
})
const moversMarket = ref('KOSPI')
const moversTab = ref('up')
const moversEl = ref(null)

async function loadMovers (m) {
  const s = mv[m]
  if (!s || s.loading) return
  s.loading = true
  try {
    const r = await api.get('/api/market/movers', { params: { market: m }, timeout: 45000 })
    s.data = (r && r.data) || null
    s.error = ''
  } catch (e) {
    s.error = errMsg(e)
  } finally {
    s.loading = false
  }
}
const moverRows = computed(() => {
  const d = mv[moversMarket.value].data
  const rows = d && d[moversTab.value]
  return Array.isArray(rows) ? rows.filter(r => r && r.ticker) : []
})
const moverError = computed(() => {
  const s = mv[moversMarket.value]
  return (s.data && s.data[moversTab.value + '_error']) || s.error || ''
})
function breadthOf (m) {
  const b = mv[m] && mv[m].data && mv[m].data.breadth
  if (!b) return null
  const up = toNum(b.up) || 0
  const down = toNum(b.down) || 0
  const flat = toNum(b.flat) || 0
  const tot = up + down + flat
  if (!tot) return null
  return {
    up, down, flat, sample: toNum(b.sample) || tot,
    upW: (up / tot) * 100, downW: (down / tot) * 100, flatW: (flat / tot) * 100
  }
}
const curBreadth = computed(() => breadthOf(moversMarket.value))

/* ───────────────────────── 지수 카드 ───────────────────────── */
const hover = reactive({ kospi: -1, kosdaq: -1 })

function buildIndexCard (key) {
  const market = key === 'kospi' ? 'KOSPI' : 'KOSDAQ'
  const base = { key, market, name: KNAME[key], code: market }
  const o = overview.value
  if (!o) return { ...base, loading: !ovError.value, error: ovError.value }
  const k = o.korea
  if (!k || k.error) return { ...base, error: (k && k.error) || '국내 지수 데이터를 불러오지 못했습니다' }
  const d = k[key] || {}
  const price = toNum(d.price)
  if (price == null) return { ...base, name: d.name || base.name, error: d.error || '지수 데이터가 없습니다' }

  const raw = Array.isArray(d.spark) ? d.spark : []
  const dates = Array.isArray(d.spark_dates) ? d.spark_dates : []
  const pairs = raw.map((v, i) => [toNum(v), dates[i]]).filter(p => p[0] != null).slice(-IDX_DAYS)
  const vals = pairs.map(p => p[0])
  const geom = sparkGeom(vals, IDX_W, IDX_H, 0, 10)
  const hi = vals.length ? Math.max(...vals) : null
  const lo = vals.length ? Math.min(...vals) : null
  const change = toNum(d.change)
  const pct = toNum(d.pct)
  const prev = change != null ? price - change : null
  const prevY = geom && prev != null && prev >= geom.min && prev <= geom.max ? geom.y(prev) : null
  const sub = key === 'kospi' && k.kospi200 && toNum(k.kospi200.price) != null
    ? { name: k.kospi200.name || KNAME.kospi200, price: k.kospi200.price, pct: k.kospi200.pct }
    : null
  return {
    ...base,
    name: d.name || base.name,
    code: d.code || base.code,
    status: statusKo(d.status),
    price, change, pct,
    spark: vals,
    dates: pairs.map(p => p[1]),
    geom,
    trend: dir(change),
    prevY,
    hi, lo,
    hiIdx: hi != null ? vals.indexOf(hi) : -1,
    loIdx: lo != null ? vals.indexOf(lo) : -1,
    days: vals.length,
    periodRet: vals.length > 1 && vals[0] ? (vals[vals.length - 1] / vals[0] - 1) * 100 : null,
    fromHigh: hi ? (price / hi - 1) * 100 : null,
    rangeText: pairs.length > 1
      ? `${fmtDate8(pairs[0][1])} ~ ${fmtDate8(pairs[pairs.length - 1][1])} · 일봉 ${vals.length}개${prevY != null ? ' · 점선 = 전일 종가' : ''}`
      : '',
    breadth: breadthOf(market),
    sub,
    error: ''
  }
}
const indexCards = computed(() => ['kospi', 'kosdaq'].map(buildIndexCard))
const idxHover = computed(() => {
  const out = {}
  for (const c of indexCards.value) {
    const i = hover[c.key]
    if (!c.geom || i == null || i < 0 || !c.geom.pts[i]) { out[c.key] = null; continue }
    const v = c.spark[i]
    const pv = i > 0 ? c.spark[i - 1] : null
    out[c.key] = {
      x: (c.geom.pts[i][0] / IDX_W) * 100,
      y: (c.geom.pts[i][1] / IDX_H) * 100,
      v,
      date: fmtDate8(c.dates[i]),
      chg: pv ? (v / pv - 1) * 100 : null
    }
  }
  return out
})
function onIdxHover (c, e) {
  const n = c.spark ? c.spark.length : 0
  if (n < 2 || !e.currentTarget) return
  const rect = e.currentTarget.getBoundingClientRect()
  if (!rect.width) return
  const r = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
  const i = Math.round(r * (n - 1))
  if (hover[c.key] !== i) hover[c.key] = i
}
function markStyle (c, i) {
  if (!c.geom || i == null || i < 0 || !c.geom.pts[i]) return { display: 'none' }
  const px = (c.geom.pts[i][0] / IDX_W) * 100
  const py = (c.geom.pts[i][1] / IDX_H) * 100
  return {
    left: px + '%',
    top: py + '%',
    transform: px > 55 ? 'translate(calc(-100% - 7px), -50%)' : 'translate(7px, -50%)'
  }
}
function dotStyle (c, i) {
  if (!c.geom || i == null || i < 0 || !c.geom.pts[i]) return { display: 'none' }
  return { left: (c.geom.pts[i][0] / IDX_W) * 100 + '%', top: (c.geom.pts[i][1] / IDX_H) * 100 + '%' }
}
function goIndexMovers (market) {
  moversMarket.value = market
  const el = moversEl.value
  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function scrollTop () {
  if (typeof window !== 'undefined') window.scrollTo({ top: 0, behavior: 'smooth' })
}

/* ───────────────────────── 글로벌 ───────────────────────── */
const globalError = computed(() => {
  const o = overview.value
  if (!o) return ovError.value
  return Array.isArray(o.global) ? '' : ((o.global && o.global.error) || '해외 시세를 불러오지 못했습니다')
})
const globalGroups = computed(() => {
  const o = overview.value
  const arr = o && Array.isArray(o.global) ? o.global.filter(Boolean) : []
  const known = new Set(GLOBAL_GROUPS.flatMap(g => g.kinds))
  const groups = GLOBAL_GROUPS.map(g => ({ ...g, items: arr.filter(x => g.kinds.includes(x.kind)) }))
  const rest = arr.filter(x => !known.has(x.kind))
  if (rest.length) groups.push({ key: 'etc', label: '기타', kinds: [], items: rest })
  return groups
    .filter(g => g.items.length)
    .map(g => ({
      ...g,
      items: g.items.map(x => ({
        ...x,
        name: x.name || x.symbol,
        priceText: x.error ? '—' : fmtGlobalPrice(x),
        path: x.error ? '' : sparkLine(x.spark, 64, 22, 2),
        trend: dir(x.pct)
      }))
    }))
})

/* ───────────────────────── 암호화폐 ───────────────────────── */
const crypto = computed(() => {
  const o = overview.value
  if (!o) return { loading: !ovError.value, error: ovError.value, coins: [], g: {} }
  const c = o.crypto
  if (!c || (c.error && !Array.isArray(c.coins))) {
    return { error: (c && c.error) || '암호화폐 데이터를 불러오지 못했습니다', coins: [], g: {} }
  }
  const g = c.global && typeof c.global === 'object' && !c.global.error ? c.global : {}
  return {
    source: c.source || 'CoinMarketCap',
    coins: Array.isArray(c.coins) ? c.coins.filter(Boolean) : [],
    g,
    gError: (c.global && c.global.error) || '',
    fallback: c.fallback_reason || ''
  }
})
const fearGreed = computed(() => {
  const fg = crypto.value.g && crypto.value.g.fear_greed
  const s = toNum(fg && fg.score)
  if (s == null) return null
  const score = Math.max(0, Math.min(100, Math.round(s)))
  const a = -Math.PI / 2 + (score / 100) * Math.PI
  const seg = Math.min(4, Math.floor(score / 20))
  return {
    score,
    label: fgLabel(fg.label, score),
    seg,
    color: GAUGE_COLORS[seg],
    nx: 54 * Math.sin(a),
    ny: -54 * Math.cos(a)
  }
})

/* ───────────────────────── 뉴스 ───────────────────────── */
const newsTab = ref('domestic')
const news = reactive(Object.fromEntries(NEWS_TABS.map(t => [t.key, { items: [], source: '', loading: false, error: '', loaded: false }])))

async function loadNews (cat, force = false) {
  const s = news[cat]
  if (!s || s.loading || (s.loaded && !force)) return
  s.loading = true
  try {
    const r = await api.get('/api/market/news', { params: { cat }, timeout: 30000 })
    const d = (r && r.data) || {}
    s.items = Array.isArray(d.items) ? d.items.filter(n => n && n.title) : []
    s.source = d.source || ''
    s.error = !s.items.length && d.error ? String(d.error) : ''
    s.loaded = true
  } catch (e) {
    s.error = errMsg(e)
  } finally {
    s.loading = false
  }
}
function selectNews (k) {
  newsTab.value = k
  loadNews(k)
}

/* ───────────────────────── 일정 ───────────────────────── */
const cal = reactive({ events: [], note: '', loading: false, error: '' })
async function loadCalendar () {
  if (cal.loading) return
  cal.loading = true
  try {
    const r = await api.get('/api/market/calendar', { timeout: 30000 })
    const d = (r && r.data) || {}
    cal.events = Array.isArray(d.events) ? d.events.filter(e => e && e.date) : []
    cal.note = d.note || ''
    cal.error = ''
  } catch (e) {
    cal.error = errMsg(e)
  } finally {
    cal.loading = false
  }
}
const calGroups = computed(() => {
  const m = new Map()
  for (const e of cal.events) {
    if (!m.has(e.date)) m.set(e.date, { date: e.date, weekday: e.weekday || '', dday: toNum(e.dday), items: [] })
    m.get(e.date).items.push(e)
  }
  return [...m.values()]
})
function ddayText (d) {
  if (d == null) return ''
  if (d === 0) return 'D-DAY'
  return d > 0 ? `D-${d}` : `D+${-d}`
}
function flagOf (r) {
  return r === 'KR' ? '🇰🇷' : r === 'US' ? '🇺🇸' : (r || '🌐')
}
function impOf (v) {
  const n = toNum(v)
  return n == null ? 1 : Math.max(1, Math.min(3, Math.round(n)))
}
function catClass (c) {
  return { 통화정책: 'amber', 경제지표: 'blue', 실적: 'green', 파생: 'violet', 휴장: 'muted' }[c] || 'muted'
}

/* ───────────────────────── 조건검색 ───────────────────────── */
const defs = reactive({ conditions: [], presets: [], loading: false, error: '' })
const scr = reactive({
  market: 'ALL',
  size: 200,
  conds: [],
  expr: '',
  exprMode: 'auto', // auto: 조건 추가·삭제 시 자동 생성 | preset: 프리셋 식을 보존하며 갱신 | user: 사용자가 편집 → 건드리지 않음
  presetId: '',
  presetDirty: false,
  running: false,
  startedAt: 0,
  result: null,
  error: '',
  sortKey: 'score',
  sortDir: -1,
  scope: null, // 오늘의 테마 → { name, no, codes, note } : 이 종목들만 검사
  pendingRun: false
})
const exprInput = ref(null)
const scrEl = ref(null)
const exprCaret = ref(null)

async function loadDefs () {
  if (defs.loading) return
  defs.loading = true
  try {
    const r = await api.get('/api/market/screener/definitions', { timeout: 20000 })
    const d = (r && r.data) || {}
    defs.conditions = Array.isArray(d.conditions) ? d.conditions.filter(c => c && c.type) : []
    defs.presets = Array.isArray(d.presets) ? d.presets.filter(p => p && p.id) : []
    defs.error = ''
    if (!scr.conds.length && defs.presets.length) loadPreset(defs.presets[0])
  } catch (e) {
    defs.error = errMsg(e)
  } finally {
    defs.loading = false
  }
}
const condByType = computed(() => Object.fromEntries(defs.conditions.map(c => [c.type, c])))
function condDef (type) { return condByType.value[type] || null }
function stageOf (type) { const d = condDef(type); return (d && toNum(d.stage)) || 1 }
function stageLabel (s) { return s === 3 ? '재무' : s === 2 ? '차트' : '시세' }
function paramLabel (k) { return PARAM_KO[k] || k }

const catalog = computed(() => {
  const groups = new Map()
  for (const c of defs.conditions) {
    const g = c.group || '기타'
    if (!groups.has(g)) groups.set(g, [])
    groups.get(g).push(c)
  }
  const ordered = GROUP_ORDER.filter(g => groups.has(g)).map(g => ({ group: g, items: groups.get(g) }))
  for (const [g, items] of groups) if (!GROUP_ORDER.includes(g)) ordered.push({ group: g, items })
  return ordered
})

function autoExpr () {
  return scr.conds.map(c => c.letter).sort().join(' and ')
}
function markDirty () {
  if (scr.presetId) scr.presetDirty = true
}
/* 다음 문자: 사용 중인 가장 뒤 문자의 다음 (삭제된 문자를 재사용하지 않아 사용자가 편집한 식의 의미가 바뀌지 않음) */
function nextLetter () {
  const used = new Set(scr.conds.map(c => c.letter))
  const maxIdx = Math.max(-1, ...scr.conds.map(c => LETTERS.indexOf(c.letter)))
  return LETTERS.slice(maxIdx + 1).find(l => !used.has(l)) || LETTERS.find(l => !used.has(l)) || null
}
function addCond (type) {
  if (scr.conds.length >= MAX_CONDS) return
  const def = condDef(type)
  if (!def) return
  const L = nextLetter()
  if (!L) return
  scr.conds.push({ letter: L, type, params: { ...(def.params || {}) } })
  const cur = scr.expr.trim()
  if (scr.exprMode === 'auto' || !cur) {
    scr.expr = autoExpr()
    scr.exprMode = 'auto'
  } else if (scr.exprMode === 'preset') {
    scr.expr = /\bor\b/i.test(cur) ? `(${cur}) and ${L}` : `${cur} and ${L}`
  } // user 모드: 사용자가 편집한 식은 그대로 둔다 (미사용 조건 안내가 표시됨)
  markDirty()
}
function removeCond (L) {
  scr.conds = scr.conds.filter(c => c.letter !== L)
  if (!scr.conds.length) {
    scr.expr = ''
    scr.exprMode = 'auto'
  } else if (scr.exprMode === 'auto') {
    scr.expr = autoExpr()
  } else if (scr.exprMode === 'preset') {
    const next = removeLetterFromExpr(scr.expr, L)
    if (next && !validateExpr(next, new Set(scr.conds.map(c => c.letter)))) {
      scr.expr = next
    } else {
      scr.expr = autoExpr()
      scr.exprMode = 'auto'
    }
  } // user 모드: 그대로 두고 검증 메시지로 '정의되지 않은 조건'을 알림
  markDirty()
}
function clearConds () {
  scr.conds = []
  scr.expr = ''
  scr.exprMode = 'auto'
  scr.presetId = ''
  scr.presetDirty = false
}
function loadPreset (p) {
  if (!p) return
  const conds = p.conditions && typeof p.conditions === 'object' ? p.conditions : {}
  scr.conds = Object.keys(conds)
    .map(k => String(k).toUpperCase().slice(0, 1))
    .sort()
    .map(L => {
      const spec = conds[L] || conds[L.toLowerCase()] || {}
      const def = condDef(spec.type)
      return def ? { letter: L, type: spec.type, params: { ...(def.params || {}), ...(spec.params || {}) } } : null
    })
    .filter(Boolean)
    .slice(0, MAX_CONDS)
  scr.expr = p.expr || autoExpr()
  scr.exprMode = scr.expr === autoExpr() ? 'auto' : 'preset'
  scr.presetId = p.id
  scr.presetDirty = false
  exprCaret.value = null
}
function setParam (c, k, e) {
  const raw = e && e.target ? String(e.target.value).trim() : ''
  if (raw === '') c.params[k] = null
  else if (Number.isFinite(Number(raw))) c.params[k] = Number(raw)
  markDirty()
}
function cleanParams (p) {
  const out = {}
  for (const [k, v] of Object.entries(p || {})) {
    out[k] = v === '' || v == null || !Number.isFinite(Number(v)) ? null : Number(v)
  }
  return out
}
function onExprInput (e) {
  scr.expr = e.target.value
  scr.exprMode = 'user'
  saveCaret(e)
  markDirty()
}
function saveCaret (e) {
  const el = e && e.target
  if (el && typeof el.selectionStart === 'number') exprCaret.value = el.selectionStart
}
function resetExpr () {
  scr.expr = autoExpr()
  scr.exprMode = 'auto'
  exprCaret.value = null
}
function insertToken (tok) {
  const s = scr.expr || ''
  const pos = exprCaret.value == null ? s.length : Math.max(0, Math.min(s.length, exprCaret.value))
  const norm = t => t.replace(/\s+/g, ' ').replace(/\(\s+/g, '(').replace(/\s+\)/g, ')')
  const left = norm((s.slice(0, pos) + ' ' + tok + ' ').replace(/^\s+/, ''))
  const full = norm(left + s.slice(pos)).trim()
  scr.expr = full
  scr.exprMode = 'user'
  const caret = Math.min(left.length, full.length)
  exprCaret.value = caret
  markDirty()
  nextTick(() => {
    const el = exprInput.value
    if (el && typeof el.focus === 'function') {
      el.focus()
      try { el.setSelectionRange(caret, caret) } catch (err) { /* 일부 브라우저 미지원 */ }
    }
  })
}

/* 논리식 토큰화 (백엔드 eval_expr 문법과 동일: 대문자 A~Z, and/or/not, 괄호) */
function tokenizeExpr (expr) {
  const s = String(expr || '').trim()
  const toks = []
  let i = 0
  while (i < s.length) {
    const ch = s[i]
    if (/\s/.test(ch)) { i++; continue }
    if (ch === '(' || ch === ')') { toks.push(ch); i++; continue }
    const m = /^(and|or|not)(?![A-Za-z0-9_])/i.exec(s.slice(i))
    if (m) { toks.push(m[1].toLowerCase()); i += m[1].length; continue }
    const nx = s[i + 1] || ''
    if (/[A-Z]/.test(ch) && !/[A-Za-z0-9_]/.test(nx)) { toks.push(ch); i++; continue }
    if (/[a-z]/.test(ch) && !/[A-Za-z0-9_]/.test(nx)) return { toks, error: `조건 문자는 대문자로 입력하세요 ("${ch}" → "${ch.toUpperCase()}")` }
    return { toks, error: `알 수 없는 토큰: "${s.slice(i, i + 8)}"` }
  }
  return { toks, error: '' }
}
function joinTokens (toks) {
  return toks.join(' ').replace(/\(\s+/g, '(').replace(/\s+\)/g, ')')
}
/* 프리셋 식에서 조건 문자 하나를 제거 (결합력이 강한 and 를 우선 제거해 나머지 논리를 보존) */
function removeLetterFromExpr (expr, L) {
  const { toks, error } = tokenizeExpr(expr)
  if (error) return null
  const t = toks.slice()
  const isOp = x => x === 'and' || x === 'or'
  let i
  while ((i = t.indexOf(L)) !== -1) {
    let start = i
    let end = i
    while (start > 0 && t[start - 1] === 'not') start--
    const prev = t[start - 1]
    const next = t[end + 1]
    if (prev === 'and') start--
    else if (next === 'and') end++
    else if (prev === 'or') start--
    else if (next === 'or') end++
    t.splice(start, end - start + 1)
  }
  let changed = true
  while (changed) {
    changed = false
    for (let k = 0; k < t.length && !changed; k++) {
      const a = t[k]
      const b = t[k + 1]
      if (a === '(' && b === ')') { t.splice(k, 2); changed = true } else if (a === '(' && /^[A-Z]$/.test(b || '') && t[k + 2] === ')') { t.splice(k + 2, 1); t.splice(k, 1); changed = true } else if (isOp(a) && (k === 0 || t[k - 1] === '(' || k === t.length - 1 || b === ')' || isOp(b))) { t.splice(k, 1); changed = true } else if (a === 'not' && (b === undefined || b === ')' || isOp(b))) { t.splice(k, 1); changed = true }
    }
  }
  return joinTokens(t)
}
/* 논리식 검증 → 오류 메시지 (없으면 '') */
function validateExpr (expr, letters) {
  if (!String(expr || '').trim()) return '조건식을 입력하세요'
  const { toks, error } = tokenizeExpr(expr)
  if (error) return error
  let p = 0
  const undef = new Set()
  function factor () {
    const t = toks[p]
    if (t === undefined) return '조건식이 불완전합니다 (끝에 조건이 필요합니다)'
    if (t === 'not') { p++; return factor() }
    if (t === '(') {
      p++
      const e = orExpr()
      if (e) return e
      if (toks[p] !== ')') return '괄호 짝이 맞지 않습니다'
      p++
      return ''
    }
    if (/^[A-Z]$/.test(t)) {
      p++
      if (!letters.has(t)) undef.add(t)
      return ''
    }
    return t === ')' ? '괄호 짝이 맞지 않습니다' : `"${t}" 앞에 조건이 필요합니다`
  }
  function andExpr () {
    let e = factor()
    while (!e && toks[p] === 'and') { p++; e = factor() }
    return e
  }
  function orExpr () {
    let e = andExpr()
    while (!e && toks[p] === 'or') { p++; e = andExpr() }
    return e
  }
  const err = orExpr()
  if (err) return err
  if (p < toks.length) {
    return toks[p] === ')' ? '괄호 짝이 맞지 않습니다' : `"${toks[p]}" 앞에 and / or 연결이 필요합니다`
  }
  if (undef.size) return `정의되지 않은 조건: ${[...undef].join(', ')}`
  return ''
}
const exprError = computed(() => (scr.conds.length ? validateExpr(scr.expr, new Set(scr.conds.map(c => c.letter))) : ''))
const unusedLetters = computed(() => {
  const used = new Set(String(scr.expr || '').match(/\b[A-Z]\b/g) || [])
  return scr.conds.map(c => c.letter).filter(l => !used.has(l))
})
const canRun = computed(() => !scr.running && scr.conds.length > 0 && !exprError.value && !defs.error)

async function runScreener () {
  if (!canRun.value) return
  scr.running = true
  scr.error = ''
  scr.startedAt = Date.now()
  const conditions = {}
  for (const c of scr.conds) conditions[c.letter] = { type: c.type, params: cleanParams(c.params) }
  try {
    const body = { market: scr.market, size: scr.size, conditions, expr: scr.expr.trim(), limit: 40 }
    if (scr.scope && scr.scope.codes.length) body.codes = scr.scope.codes
    const scopeKey = body.codes ? body.codes.join(',') : ''
    const r = await api.post('/api/market/screener', body, { timeout: 180000 })
    // 검색 도중 테마 범위가 바뀌었다면 이전 범위의 결과는 버림 (finally 에서 새 범위로 다시 실행)
    if ((scr.scope ? scr.scope.codes.join(',') : '') !== scopeKey) return
    scr.result = (r && r.data) || null
    scr.sortKey = 'score'
    scr.sortDir = -1
  } catch (e) {
    scr.error = errMsg(e)
  } finally {
    scr.running = false
    if (scr.pendingRun) {
      scr.pendingRun = false
      if (canRun.value) runScreener()
    }
  }
}
const scanPhases = computed(() => {
  const stages = new Set(scr.conds.map(c => stageOf(c.type)))
  const ph = [scr.scope ? `유니버스 구성 · ${scr.scope.name} 테마 ${scr.scope.codes.length}종목 시세 조회` : `유니버스 구성 · 시총 상위 ${scr.size}종목`, '1차 필터 · 시세 조건 평가']
  if (stages.has(2)) ph.push('일봉 130개 로딩 · 이평·RSI·MACD·볼린저 계산')
  if (stages.has(3)) ph.push('재무 지표 조회 · PER·PBR')
  ph.push('조건식 평가 · 신호 점수 산출')
  return ph
})
const scrRows = computed(() => {
  const list = scr.result && Array.isArray(scr.result.results) ? scr.result.results.filter(Boolean) : []
  const max = Math.max(1, ...list.map(r => toNum(r.score) || 0))
  const rows = list.map(r => ({
    ...r,
    scoreW: Math.max(4, ((toNum(r.score) || 0) / max) * 100),
    sparkD: sparkLine(r.spark, 90, 26, 2),
    trend: trendOf(r.spark),
    signals: Array.isArray(r.signals) ? r.signals.filter(Boolean) : []
  }))
  const k = scr.sortKey
  const d = scr.sortDir
  return rows.sort((a, b) => {
    const x = toNum(a[k])
    const y = toNum(b[k])
    if (x == null && y == null) return 0
    if (x == null) return 1
    if (y == null) return -1
    return (x - y) * d
  })
})
const scrHasFund = computed(() => scrRows.value.some(r => toNum(r.per) != null || toNum(r.pbr) != null))
function sortBy (k) {
  if (scr.sortKey === k) scr.sortDir = -scr.sortDir
  else { scr.sortKey = k; scr.sortDir = -1 }
}
function sortMark (k) {
  return scr.sortKey === k ? (scr.sortDir < 0 ? '▼' : '▲') : ''
}
function sigTip (s) {
  return `${s.letter} · ${s.label}${s.ok ? ' 충족' : ' 미충족'}${s.note ? ' — ' + s.note : ''}`
}
function funnelW (v) {
  const u = toNum(scr.result && scr.result.universe)
  const n = toNum(v)
  if (!u || n == null) return 0
  return Math.max(n > 0 ? 2 : 0, Math.min(100, (n / u) * 100))
}

/* 진행 표시 (실제 진행률이 없으므로 시간 기반 점근 곡선) */
function fakeProgress (now, start, tau) {
  if (!start) return 0
  const t = Math.max(0, (now - start) / 1000)
  return Math.min(96, 96 * (1 - Math.exp(-t / tau)))
}
function phaseState (i, now, start, n, tau) {
  const cur = Math.min(n - 1, Math.floor((fakeProgress(now, start, tau) / 96) * n))
  return i < cur ? 'done' : i === cur ? 'active' : ''
}
function elapsedSec (now, start, d = 1) {
  return start ? Math.max(0, (now - start) / 1000).toFixed(d) : (0).toFixed(d)
}

/* ───────────────────────── AI 추천 ───────────────────────── */
const rec = reactive({ data: null, loading: false, error: '', startedAt: 0 })
async function loadRecommend () {
  if (rec.loading) return
  rec.loading = true
  rec.error = ''
  rec.startedAt = Date.now()
  try {
    const r = await api.get('/api/market/recommend', { params: { market: 'ALL' }, timeout: 300000 })
    rec.data = (r && r.data) || null
  } catch (e) {
    rec.error = errMsg(e)
  } finally {
    rec.loading = false
  }
}
const recPresets = computed(() => (rec.data && Array.isArray(rec.data.presets) ? rec.data.presets.filter(Boolean) : []))
const recItems = computed(() => {
  const items = rec.data && Array.isArray(rec.data.items) ? rec.data.items.filter(it => it && it.ticker) : []
  return items.map((it, i) => {
    const g = sparkGeom(it.spark, 240, 48, 0, 4)
    return {
      ...it,
      rank: i + 1,
      line: g ? g.line : '',
      area: g ? g.area : '',
      trend: trendOf(it.spark),
      presets: Array.isArray(it.presets) ? it.presets : [],
      tags: Array.isArray(it.tags) ? it.tags : []
    }
  })
})
const recPhaseNames = computed(() => {
  const names = defs.presets.filter(p => p.id !== 'value').map(p => p.name)
  return names.length ? names : ['추세 전략', '골든크로스', '신고가', '과매도 반등', 'MACD', '눌림목', '돌파']
})
function recPhaseText (now) {
  const names = recPhaseNames.value
  const i = Math.min(names.length - 1, Math.floor((fakeProgress(now, rec.startedAt, 40) / 96) * names.length))
  return `전략 ${i + 1}/${names.length} · ${names[i]} 스캔`
}

/* ───────────────────────── 오늘의 테마 → 스크리너 ───────────────────────── */
/* 테마 구성 종목은 중소형주가 많아 기본 프리셋(시총·거래대금 하한)이 대부분을 걸러내므로,
   사용자가 조건을 손대지 않았다면 차트 위주 '테마 점검' 조건식으로 바꿔 검사합니다. */
const THEME_PRESET = {
  id: 'theme_check',
  name: '테마 점검 (차트)',
  conditions: {
    A: { type: 'above_ma', params: { ma: 20 } },
    B: { type: 'ma_align', params: { short: 5, mid: 20, long: 60 } },
    C: { type: 'vol_surge', params: { ratio: 1.5, avg: 20 } },
    D: { type: 'macd_cross', params: { within: 3 } },
    E: { type: 'rsi', params: { min: 30, max: 75 } }
  },
  expr: 'A and (B or C or D) and E'
}
const scopeMissing = computed(() => {
  const r = scr.result
  return r && r.scope === 'codes' && Array.isArray(r.codes_missing) ? r.codes_missing.length : 0
})
function isUntouchedDefault () {
  if (!scr.conds.length) return true
  const first = defs.presets[0]
  return !!first && scr.presetId === first.id && !scr.presetDirty
}
async function screenTheme (payload) {
  const codes = payload && Array.isArray(payload.codes) ? [...new Set(payload.codes.map(String))].slice(0, 200) : []
  if (!codes.length) return
  if (!defs.conditions.length) await loadDefs()
  let note = '현재 조건식으로 검사'
  if (isUntouchedDefault() && defs.conditions.length) {
    loadPreset(THEME_PRESET)
    note = '조건식: 테마 점검(20일선 위 · 정배열/거래량 급증/MACD · RSI 30~75) — 수정 가능'
  }
  scr.scope = { name: String(payload.name || '테마'), no: payload.no || null, codes, note }
  scr.result = null
  scr.error = ''
  await nextTick()
  const el = scrEl.value
  if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  if (scr.running) scr.pendingRun = true // 진행 중인 검색이 끝나면 테마 범위로 다시 실행
  else if (canRun.value) runScreener()
}
function clearScope () {
  scr.scope = null
  scr.result = null
  scr.error = ''
}

/* ───────────────────────── 네비게이션 ───────────────────────── */
function goAnalyze (row) {
  if (!row || !row.ticker) return
  const query = { ticker: String(row.ticker) }
  if (row.name) query.name = String(row.name)
  if (row.market) query.market = String(row.market)
  router.push({ path: '/stock', query })
}

/* ───────────────────────── 새로고침 · 라이프사이클 ───────────────────────── */
let refreshTimer = null
let newsTimer = null
let recDelay = null
let unmounted = false

function pageHidden () {
  return typeof document !== 'undefined' && document.hidden
}
function autoRefresh () {
  if (pageHidden()) return
  loadOverview()
  loadMovers('KOSPI')
  loadMovers('KOSDAQ')
}
async function refreshAll () {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await Promise.allSettled([
      loadOverview(),
      loadMovers('KOSPI'),
      loadMovers('KOSDAQ'),
      loadNews(newsTab.value, true),
      loadCalendar()
    ])
  } finally {
    refreshing.value = false
  }
}

onMounted(async () => {
  refreshTimer = setInterval(autoRefresh, 60000)
  newsTimer = setInterval(() => { if (!pageHidden()) loadNews(newsTab.value, true) }, 300000)
  await Promise.allSettled([
    loadOverview(),
    loadMovers('KOSPI'),
    loadMovers('KOSDAQ'),
    loadDefs(),
    loadCalendar(),
    loadNews('domestic')
  ])
  if (!unmounted) recDelay = setTimeout(loadRecommend, 600)
})
onBeforeUnmount(() => {
  unmounted = true
  if (refreshTimer) clearInterval(refreshTimer)
  if (newsTimer) clearInterval(newsTimer)
  if (recDelay) clearTimeout(recDelay)
  refreshTimer = newsTimer = recDelay = null
})
</script>

<style scoped>
/* ═════════════ 토큰 · 베이스 ═════════════ */
.dash {
  --bg: #0b0f14;
  --panel: #0f1419;
  --panel-2: #121821;
  --border: #2a3041;
  --subtle: #1e2533;
  --text: #e2e8f0;
  --text-2: #c9d4e0;
  --muted: #8892a4;
  --primary: #3b82f6;
  --up: #ef4444;
  --down: #3b82f6;
  --amber: #f59e0b;
  --green: #10b981;
  --violet: #a78bfa;
  --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: 'Noto Sans KR', 'Inter', -apple-system, 'Apple SD Gothic Neo', BlinkMacSystemFont, 'Segoe UI', sans-serif;

  min-height: 100vh;
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 13px;
  line-height: 1.45;
  color-scheme: dark;
  overflow-x: clip;
  -webkit-font-smoothing: antialiased;
}
.dash *, .dash *::before, .dash *::after { box-sizing: border-box; }
.svg-defs { position: absolute; width: 0; height: 0; overflow: hidden; }
button {
  font: inherit;
  color: inherit;
  background: none;
  border: 0;
  padding: 0;
  cursor: pointer;
}
button:disabled { cursor: not-allowed; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, [role='button']:focus-visible {
  outline: 2px solid rgba(59, 130, 246, 0.65);
  outline-offset: 1px;
}
h2, h3, p, dl, dd, ol, ul { margin: 0; padding: 0; }
ol, ul { list-style: none; }

.mono { font-family: var(--mono); font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
.muted { color: var(--muted); }
.small { font-size: 11px; }
.up { color: var(--up); }
.down { color: var(--down); }
.flat { color: var(--muted); }
.accent { color: var(--primary); font-weight: 600; }
.link { cursor: pointer; }
.link:hover { color: #93c5fd; }

.sec-label {
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--muted);
  white-space: nowrap;
}

/* ═════════════ 상단 바 ═════════════ */
.topbar {
  position: sticky;
  top: 0;
  z-index: 50;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  min-height: 54px;
  padding: 8px 20px;
  background: rgba(11, 15, 20, 0.9);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
}
.tb-left { display: flex; align-items: center; gap: 22px; min-width: 0; }
.brand { display: flex; align-items: center; gap: 10px; cursor: pointer; flex: none; }
.logo { display: block; filter: drop-shadow(0 2px 8px rgba(37, 99, 235, 0.35)); }
.brand-text { display: flex; flex-direction: column; line-height: 1.15; }
.wordmark { font-family: 'Inter', var(--sans); font-size: 16px; font-weight: 700; letter-spacing: -0.02em; color: var(--text); }
.wordmark b { color: #60a5fa; font-weight: 700; }
.brand-sub { font-size: 10.5px; color: var(--muted); letter-spacing: 0.04em; }
.tb-nav { display: flex; gap: 2px; min-width: 0; overflow-x: auto; scrollbar-width: none; }
.tb-nav::-webkit-scrollbar { display: none; }
.nav-btn {
  padding: 7px 12px;
  border-radius: 6px;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--muted);
  white-space: nowrap;
  transition: color 0.15s, background 0.15s;
}
.nav-btn:hover { color: var(--text); background: var(--subtle); }
.nav-btn.active { color: var(--text); background: rgba(59, 130, 246, 0.14); box-shadow: inset 0 -2px 0 var(--primary); }

.tb-right { display: flex; align-items: center; gap: 12px; flex: none; }
.clock { display: inline-flex; align-items: baseline; gap: 7px; font-size: 12px; color: var(--text-2); }
.clock-date { color: var(--muted); }
.clock-time { font-size: 14px; font-weight: 600; color: var(--text); }
.clock-tz { font-size: 9.5px; color: var(--muted); letter-spacing: 0.1em; }
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 600;
  border: 1px solid var(--border);
  color: var(--muted);
  white-space: nowrap;
}
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.status-pill.live { color: #fca5a5; border-color: rgba(239, 68, 68, 0.45); background: rgba(239, 68, 68, 0.08); }
.status-pill.live .status-dot { background: var(--up); animation: pulse 1.6s ease-out infinite; }
.status-pill.warn { color: #fcd34d; border-color: rgba(245, 158, 11, 0.45); background: rgba(245, 158, 11, 0.08); }
.status-pill.warn .status-dot { background: var(--amber); }
.upd { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); white-space: nowrap; }
.upd-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--green); opacity: 0.8; }
.upd.busy .upd-dot { background: var(--amber); animation: blink 0.8s ease-in-out infinite; }
.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: 6px;
  border: 1px solid var(--border);
  color: var(--text-2);
  transition: background 0.15s, border-color 0.15s;
  flex: none;
}
.icon-btn:hover:not(:disabled) { background: var(--subtle); border-color: #3a4256; }
.icon-btn:disabled { opacity: 0.6; }
.spin { animation: spin 0.9s linear infinite; }

/* ═════════════ 티커 테이프 ═════════════ */
.tape {
  position: relative;
  height: 32px;
  overflow: hidden;
  background: #0d1218;
  border-bottom: 1px solid var(--border);
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 3%, #000 97%, transparent);
  mask-image: linear-gradient(90deg, transparent, #000 3%, #000 97%, transparent);
}
.tape-empty { display: flex; align-items: center; padding: 0 20px; }
.tape-track { display: flex; width: max-content; height: 100%; animation: tape 80s linear infinite; }
.tape:hover .tape-track { animation-play-state: paused; }
.tape-group { display: flex; align-items: center; height: 100%; }
.tape-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  height: 100%;
  padding: 0 18px;
  border-right: 1px solid var(--subtle);
  white-space: nowrap;
  font-size: 12px;
}
.tape-name { color: var(--text-2); font-weight: 500; }
.tape-price { color: var(--text); }
.tape-pct { font-size: 11.5px; }

/* ═════════════ 레이아웃 ═════════════ */
.main {
  max-width: 1560px;
  margin: 0 auto;
  padding: 14px 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.row { display: grid; gap: 14px; min-width: 0; }
.row-1 { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.55fr); }
.row-2 { grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); }
.row-5 { grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr); }

/* ═════════════ 패널 공통 ═════════════ */
.panel {
  position: relative;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.panel-hd {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 10px;
  min-height: 40px;
  padding: 8px 14px;
  border-bottom: 1px solid var(--subtle);
  background: linear-gradient(180deg, #111720 0%, var(--panel) 100%);
}
.panel-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.02em;
  color: var(--text);
  white-space: nowrap;
}
.panel-title.lg { font-size: 14px; }
.t-bar { display: inline-block; width: 3px; height: 13px; border-radius: 2px; background: var(--primary); }
.t-bar.green { background: var(--green); }
.t-bar.amber { background: var(--amber); }
.hd-right { margin-left: auto; }
.hd-desc { font-size: 11.5px; min-width: 0; }
.hd-tools { display: inline-flex; align-items: center; gap: 10px; }
.code-chip {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 10.5px;
  color: var(--muted);
  border: 1px solid var(--border);
}
.mini-status { font-size: 11px; color: var(--muted); }
.link-btn { font-size: 11.5px; color: #60a5fa; white-space: nowrap; }
.link-btn:hover { color: #93c5fd; text-decoration: underline; }
.btn-ghost {
  padding: 5px 12px;
  border-radius: 6px;
  border: 1px solid var(--border);
  color: var(--text-2);
  font-size: 12px;
  white-space: nowrap;
}
.btn-ghost:hover:not(:disabled) { background: var(--subtle); }
.btn-ghost.sm { padding: 3px 10px; font-size: 11.5px; }

.state-box {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 10px;
  padding: 26px 16px;
  text-align: center;
  color: var(--muted);
  font-size: 12.5px;
}
.state-box.err { color: #fca5a5; }
.state-box.big { padding: 60px 16px; }
.inline-note { padding: 6px 14px; font-size: 11px; color: #fcd34d; border-bottom: 1px solid var(--subtle); }

.pct-pill {
  display: inline-block;
  padding: 2px 7px;
  border-radius: 4px;
  font-family: var(--mono);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  background: rgba(136, 146, 164, 0.12);
  color: var(--muted);
}
.pct-pill.up { color: var(--up); background: rgba(239, 68, 68, 0.13); }
.pct-pill.down { color: var(--down); background: rgba(59, 130, 246, 0.15); }
.pct-pill.sm { font-size: 11.5px; padding: 1px 6px; min-width: 64px; text-align: right; }

/* 탭 · 세그먼트 */
.tabs {
  display: flex;
  gap: 2px;
  padding: 0 10px;
  border-bottom: 1px solid var(--subtle);
  overflow-x: auto;
  scrollbar-width: none;
}
.tabs::-webkit-scrollbar { display: none; }
.tab {
  padding: 9px 11px 8px;
  font-size: 12.5px;
  color: var(--muted);
  border-bottom: 2px solid transparent;
  white-space: nowrap;
  transition: color 0.15s, border-color 0.15s;
}
.tab:hover { color: var(--text-2); }
.tab.on { color: var(--text); font-weight: 600; border-bottom-color: var(--primary); }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.seg button { padding: 3px 11px; font-size: 11.5px; color: var(--muted); transition: background 0.15s, color 0.15s; }
.seg button + button { border-left: 1px solid var(--border); }
.seg button:hover { color: var(--text); }
.seg button.on { background: var(--primary); color: #fff; font-weight: 600; }

/* 테이블 */
.tbl-wrap {
  overflow: auto;
  min-width: 0;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
}
.tbl-wrap::-webkit-scrollbar { width: 8px; height: 8px; }
.tbl-wrap::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
.tbl-wrap::-webkit-scrollbar-track { background: transparent; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  padding: 7px 10px;
  background: var(--panel);
  color: var(--muted);
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-align: right;
  white-space: nowrap;
  border-bottom: 1px solid var(--border);
}
.tbl td {
  padding: 6px 10px;
  border-bottom: 1px solid rgba(30, 37, 51, 0.75);
  text-align: right;
  white-space: nowrap;
  vertical-align: middle;
}
.tbl tbody tr { transition: background 0.12s; }
.tbl tbody tr:hover { background: #141b25; }
.tbl .l { text-align: left; }
.tbl .r { text-align: right; }
.tbl .c { text-align: center; }
.w-rank { width: 44px; }
.w-act { width: 56px; }
.w-act2 { width: 110px; }
.click-row { cursor: pointer; }
.nm-cell { display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }
.nm-cell .nm { color: var(--text); font-weight: 500; overflow: hidden; text-overflow: ellipsis; max-width: 200px; }
.nm-cell .code { font-size: 10.5px; color: var(--muted); }
.nm-cell.link:hover .nm { color: #93c5fd; }
.rank-num { display: inline-block; min-width: 20px; color: var(--muted); font-size: 11.5px; }
.rank-num.top { color: var(--amber); font-weight: 700; }
.th-btn { display: inline-flex; align-items: center; gap: 3px; color: inherit; font-size: inherit; letter-spacing: inherit; }
.th-btn:hover { color: var(--text); }
.th-btn i { font-style: normal; font-size: 9px; color: var(--primary); min-width: 8px; }

.go-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 5px;
  font-size: 11.5px;
  font-weight: 600;
  color: #bfdbfe;
  background: rgba(59, 130, 246, 0.14);
  border: 1px solid rgba(59, 130, 246, 0.35);
  white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.go-btn:hover { background: var(--primary); color: #fff; border-color: var(--primary); }
.go-btn.sm { padding: 2px 9px; font-size: 11px; }
.go-btn.full { width: 100%; padding: 7px 10px; margin-top: auto; }

/* ═════════════ 스켈레톤 ═════════════ */
.sk {
  display: block;
  border-radius: 4px;
  background: linear-gradient(90deg, #141b24 25%, #1c2532 37%, #141b24 63%);
  background-size: 400% 100%;
  animation: shimmer 1.4s ease infinite;
}
.sk-rows { display: flex; flex-direction: column; gap: 8px; padding: 12px 14px; }
.sk-row { height: 26px; }
.sk-h8 { height: 8px; }
.sk-h10 { height: 10px; }
.sk-h14 { height: 14px; }
.sk-h24 { height: 24px; }
.sk-h32 { height: 32px; }
.sk-chart { height: 96px; }
.sk-cell { height: 46px; }
.sk-stat { height: 72px; }
.sk-spark { height: 48px; }
.sk-tape { width: 60%; height: 12px; }
.w20 { width: 20%; } .w30 { width: 30%; } .w35 { width: 35%; } .w45 { width: 45%; } .w50 { width: 50%; }
.w60 { width: 60%; } .w70 { width: 70%; } .w80 { width: 80%; }

/* ═════════════ ROW 1 : 지수 카드 ═════════════ */
.idx-body { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px 14px; flex: 1; }
.idx-quote { display: flex; align-items: flex-end; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.idx-main { display: flex; flex-direction: column; gap: 3px; }
.idx-price { font-size: 30px; font-weight: 700; line-height: 1.05; letter-spacing: -0.03em; }
.idx-chg { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 600; }
.idx-sub {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  padding: 3px 8px;
  border: 1px solid var(--subtle);
  border-radius: 5px;
  background: var(--panel-2);
}
.idx-chart { position: relative; height: 100px; touch-action: pan-y; cursor: crosshair; }
.fill-svg { position: absolute; inset: 0; width: 100%; height: 100%; display: block; overflow: visible; }
.ref-line { stroke: #4b5568; stroke-width: 1; stroke-dasharray: 3 3; }
.chart-empty { display: flex; align-items: center; justify-content: center; height: 100%; border: 1px dashed var(--subtle); border-radius: 6px; }
.pt-mark {
  position: absolute;
  font-size: 10px;
  color: var(--text-2);
  background: rgba(15, 20, 25, 0.82);
  padding: 0 4px;
  border-radius: 3px;
  white-space: nowrap;
  pointer-events: none;
}
.pt-mark.hi { color: #fca5a5; }
.pt-mark.lo { color: #93c5fd; }
.last-dot {
  position: absolute;
  width: 7px;
  height: 7px;
  margin: -3.5px 0 0 -3.5px;
  border-radius: 50%;
  background: var(--muted);
  pointer-events: none;
}
.last-dot.up { background: var(--up); box-shadow: 0 0 0 3px rgba(239, 68, 68, 0.25); }
.last-dot.down { background: var(--down); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25); }
.xhair { position: absolute; top: 0; bottom: 0; width: 0; border-left: 1px dashed #64748b; pointer-events: none; }
.hv-dot {
  position: absolute;
  width: 9px;
  height: 9px;
  margin: -4.5px 0 0 -4.5px;
  border-radius: 50%;
  background: var(--text);
  border: 2px solid var(--panel);
  pointer-events: none;
}
.hv-tip {
  position: absolute;
  top: 0;
  transform: translateX(8px);
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 5px 8px;
  font-size: 11px;
  background: rgba(18, 24, 33, 0.95);
  border: 1px solid var(--border);
  border-radius: 5px;
  white-space: nowrap;
  pointer-events: none;
  z-index: 2;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
}
.hv-tip.flip { transform: translateX(calc(-100% - 8px)); }
.hv-tip b { font-size: 12.5px; color: var(--text); }
.idx-range { font-size: 10.5px; color: var(--muted); margin-top: -4px; }
.idx-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--subtle);
  border-radius: 6px;
  overflow: hidden;
}
.idx-stats > div { padding: 6px 8px; display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.idx-stats > div + div { border-left: 1px solid var(--subtle); }
.idx-stats dt { font-size: 10.5px; color: var(--muted); white-space: nowrap; }
.idx-stats dd { font-size: 12.5px; font-weight: 600; color: var(--text-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.breadth { display: flex; flex-direction: column; gap: 6px; }
.breadth-hd { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.breadth-bar { display: flex; height: 8px; border-radius: 4px; overflow: hidden; background: var(--subtle); }
.breadth-bar span { display: block; height: 100%; transition: width 0.5s ease; }
.b-up { background: linear-gradient(90deg, #b91c1c, var(--up)); }
.b-flat { background: #4b5568; }
.b-down { background: linear-gradient(90deg, var(--down), #1d4ed8); }
.breadth-legend { display: flex; justify-content: space-between; font-size: 11px; }

/* ═════════════ ROW 1 : 글로벌 ═════════════ */
.gl-body { display: flex; flex-direction: column; gap: 12px; padding: 12px 14px 14px; }
.gl-group { display: flex; flex-direction: column; gap: 6px; }
.gl-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px; }
.gl-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 9px;
  border-radius: 6px;
  background: var(--panel-2);
  border: 1px solid var(--subtle);
  min-width: 0;
  transition: border-color 0.15s, background 0.15s;
}
.gl-cell:hover { border-color: #334056; background: #151c27; }
.gl-cell.bad { opacity: 0.6; }
.gl-top, .gl-bot { display: flex; align-items: center; justify-content: space-between; gap: 6px; min-width: 0; }
.gl-name { font-size: 11.5px; color: var(--text-2); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.gl-pct { font-size: 11px; font-weight: 600; flex: none; }
.gl-price { font-size: 13px; font-weight: 600; color: var(--text); white-space: nowrap; }
.gl-spark { width: 56px; height: 22px; flex: none; display: block; }
.gl-spark-empty { font-size: 10px; }

/* ═════════════ ROW 2 : 주도주 ═════════════ */
.movers-panel { scroll-margin-top: 96px; }
.movers-wrap { max-height: 520px; flex: 1; }
.movers-tbl { min-width: 560px; }

/* ═════════════ ROW 2 : 암호화폐 ═════════════ */
.cr-body { display: flex; flex-direction: column; min-height: 0; flex: 1; }
.cr-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr)) minmax(150px, 1.15fr);
  gap: 1px;
  background: var(--subtle);
  border-bottom: 1px solid var(--subtle);
}
.cr-stats > .sk { border-radius: 0; }
.cr-stat, .cr-gauge { display: flex; flex-direction: column; gap: 3px; padding: 10px 12px; background: var(--panel); min-width: 0; }
.cr-stat b { font-size: 17px; font-weight: 700; color: var(--text); white-space: nowrap; }
.dom-bar { display: flex; height: 5px; border-radius: 3px; overflow: hidden; background: var(--subtle); margin: 2px 0; }
.dom-btc { background: var(--amber); }
.dom-eth { background: #818cf8; }
.cr-gauge { align-items: center; padding-bottom: 8px; }
.cr-gauge .sec-label { align-self: flex-start; }
.gauge-svg { width: 100%; max-width: 150px; height: auto; display: block; }
.gauge-svg path { transition: opacity 0.3s; }
.g-tick { fill: var(--muted); font-size: 10px; font-family: var(--mono); }
.gauge-read { display: flex; align-items: baseline; gap: 6px; margin-top: -4px; }
.gauge-read b { font-size: 18px; font-weight: 700; }
.gauge-read span { font-size: 11.5px; font-weight: 600; }
.crypto-wrap { max-height: 420px; flex: 1; }
.crypto-tbl { min-width: 520px; }
.tag-warn {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 4px;
  color: #fcd34d;
  border: 1px solid rgba(245, 158, 11, 0.4);
}

/* ═════════════ ROW 3 : 조건검색 ═════════════ */
.scr-panel { border-color: #2f3a4f; }
.scope-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 10px;
  padding: 7px 14px;
  border-bottom: 1px solid var(--subtle);
  background: rgba(167, 139, 250, 0.08);
  font-size: 12px;
}
.scope-chip { display: inline-flex; align-items: center; gap: 6px; color: var(--text); }
.scope-chip b { color: #ddd6fe; }
.scope-x { margin-left: auto; }
.blk-row.scoped { opacity: 0.55; }
.scr-hd { background: linear-gradient(90deg, rgba(16, 185, 129, 0.07), transparent 40%), linear-gradient(180deg, #111720, var(--panel)); }
.scr-body { display: grid; grid-template-columns: 380px minmax(0, 1fr); min-height: 560px; }
.scr-builder {
  display: flex;
  flex-direction: column;
  gap: 0;
  border-right: 1px solid var(--subtle);
  background: #0d1217;
  min-width: 0;
}
.blk { display: flex; flex-direction: column; gap: 7px; padding: 11px 14px; border-bottom: 1px solid var(--subtle); }
.blk-hd { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.blk-row { flex-direction: row; gap: 10px; }
.fld { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; }
.sel {
  appearance: none;
  -webkit-appearance: none;
  width: 100%;
  padding: 6px 28px 6px 9px;
  font: inherit;
  font-size: 12.5px;
  color: var(--text);
  background: var(--panel-2) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%238892a4' stroke-width='1.5' fill='none'/%3E%3C/svg%3E") no-repeat right 10px center;
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
}
.sel:hover { border-color: #3a4256; }
.tag-mod { font-size: 10px; padding: 1px 6px; border-radius: 3px; color: #fcd34d; background: rgba(245, 158, 11, 0.12); }

.preset-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 232px;
  overflow-y: auto;
  scrollbar-width: thin;
  scrollbar-color: var(--border) transparent;
  margin-right: -6px;
  padding-right: 6px;
}
.preset {
  width: 100%;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 1px 8px;
  padding: 6px 9px;
  text-align: left;
  border-radius: 6px;
  border: 1px solid transparent;
  background: var(--panel-2);
  transition: border-color 0.15s, background 0.15s;
}
.preset:hover { border-color: var(--border); background: #151c27; }
.preset.on { border-color: rgba(16, 185, 129, 0.55); background: rgba(16, 185, 129, 0.08); }
.preset-name { font-size: 12.5px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.preset-expr { grid-row: 1; grid-column: 2; font-size: 10px; color: var(--muted); align-self: center; white-space: nowrap; }
.preset-desc { grid-column: 1 / -1; font-size: 11px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.preset.on .preset-expr { color: #6ee7b7; }

.cat-group { display: flex; flex-direction: column; gap: 4px; }
.cat-title { font-size: 10.5px; color: #64748b; font-weight: 600; letter-spacing: 0.08em; }
.cat-items { display: flex; flex-wrap: wrap; gap: 4px; }
.cat-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px 3px 6px;
  font-size: 11.5px;
  color: var(--text-2);
  border-radius: 5px;
  border: 1px solid var(--border);
  background: var(--panel);
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}
.cat-item .plus { color: var(--green); font-weight: 700; font-family: var(--mono); }
.cat-item:hover:not(:disabled) { border-color: rgba(16, 185, 129, 0.6); color: var(--text); background: rgba(16, 185, 129, 0.07); }
.cat-item:disabled { opacity: 0.45; }

.empty-mini { font-size: 11.5px; color: var(--muted); padding: 10px; border: 1px dashed var(--border); border-radius: 6px; text-align: center; }
.cond-list { display: flex; flex-direction: column; gap: 6px; position: relative; }
.cond-row {
  display: grid;
  grid-template-columns: 26px minmax(0, 1fr) 22px;
  gap: 9px;
  align-items: start;
  padding: 8px 8px 8px 8px;
  border-radius: 6px;
  background: var(--panel-2);
  border: 1px solid var(--subtle);
}
.letter {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 5px;
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, #2563eb, #10b981);
}
.cond-main { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.cond-top { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; min-height: 26px; }
.cond-label { font-size: 12.5px; font-weight: 600; color: var(--text); }
.cond-unit { font-size: 10.5px; color: var(--muted); }
.stage { font-size: 9.5px; padding: 1px 5px; border-radius: 3px; border: 1px solid; margin-left: auto; }
.stage.s1 { color: #93c5fd; border-color: rgba(59, 130, 246, 0.4); }
.stage.s2 { color: #6ee7b7; border-color: rgba(16, 185, 129, 0.4); }
.stage.s3 { color: #fcd34d; border-color: rgba(245, 158, 11, 0.4); }
.cond-params { display: flex; flex-wrap: wrap; gap: 6px; }
.param { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--muted); }
.num-in {
  width: 72px;
  padding: 3px 6px;
  font-size: 12px;
  color: var(--text);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  -moz-appearance: textfield;
}
.num-in::placeholder { color: #4b5568; font-family: var(--sans); font-size: 10.5px; }
.num-in:focus { border-color: var(--primary); outline: none; }
.num-in::-webkit-outer-spin-button, .num-in::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
.cond-desc { font-size: 10.5px; color: #64748b; }
.x-btn {
  width: 22px;
  height: 22px;
  border-radius: 4px;
  font-size: 15px;
  line-height: 1;
  color: var(--muted);
  margin-top: 2px;
}
.x-btn:hover { color: #fca5a5; background: rgba(239, 68, 68, 0.12); }
.cond-enter-active, .cond-leave-active { transition: all 0.2s ease; }
.cond-enter-from, .cond-leave-to { opacity: 0; transform: translateX(-8px); }
.cond-leave-active { position: absolute; left: 0; right: 0; }
.cond-move { transition: transform 0.2s ease; }

.expr-in {
  width: 100%;
  padding: 8px 10px;
  font-size: 13px;
  color: #a7f3d0;
  background: #070a0e;
  border: 1px solid var(--border);
  border-radius: 6px;
  box-sizing: border-box;
}
.expr-in:focus { outline: none; border-color: var(--green); box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.12); }
.expr-in.bad { border-color: rgba(239, 68, 68, 0.6); color: #fecaca; }
.tok-chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
.tok {
  min-width: 28px;
  padding: 2px 8px;
  font-size: 11.5px;
  color: var(--text-2);
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--panel);
}
.tok:hover { border-color: #4b5568; color: var(--text); background: var(--subtle); }
.tok-l { color: #93c5fd; }
.tok-sep { width: 1px; height: 16px; background: var(--border); margin: 0 3px; }
.expr-msg { font-size: 11px; }
.expr-msg.ok { color: #6ee7b7; }
.expr-msg.err { color: #fca5a5; }
.expr-msg.warn { color: #fcd34d; }
.expr-msg.idle { color: var(--muted); }

.run-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 12px 14px 14px;
  padding: 10px 14px;
  border-radius: 7px;
  font-size: 13.5px;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, #2563eb, #0ea5a4);
  box-shadow: 0 4px 16px rgba(37, 99, 235, 0.25);
  transition: filter 0.15s, transform 0.1s, opacity 0.15s;
}
.run-btn:hover:not(:disabled) { filter: brightness(1.1); }
.run-btn:active:not(:disabled) { transform: translateY(1px); }
.run-btn:disabled { opacity: 0.45; box-shadow: none; }
.btn-spinner {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 2px solid rgba(255, 255, 255, 0.35);
  border-top-color: #fff;
  animation: spin 0.8s linear infinite;
}

/* 결과 영역 */
.scr-results { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.scr-idle {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 40px 20px;
  text-align: center;
}
.scr-idle h3 { font-size: 15px; font-weight: 600; color: var(--text); }
.idle-steps { display: flex; flex-direction: column; gap: 7px; text-align: left; font-size: 12.5px; color: var(--text-2); }
.idle-steps li { display: flex; align-items: center; gap: 9px; }
.idle-steps b {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  flex: none;
  border-radius: 50%;
  font-size: 11px;
  color: var(--primary);
  border: 1px solid rgba(59, 130, 246, 0.5);
}
.scr-idle p { max-width: 460px; }

.funnel {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--subtle);
  background: var(--panel-2);
}
.fn-step { display: flex; align-items: baseline; gap: 5px; }
.fn-num { font-size: 18px; font-weight: 700; color: var(--text-2); }
.fn-lbl { font-size: 11px; color: var(--muted); }
.fn-step.hit .fn-num { color: var(--green); }
.fn-arrow { color: #4b5568; font-size: 16px; }
.fn-bars { position: relative; flex: 1; min-width: 100px; max-width: 240px; height: 6px; border-radius: 3px; overflow: hidden; background: var(--subtle); }
.fb { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; transition: width 0.6s ease; }
.fb-u { width: 100%; background: #273042; }
.fb-c { background: rgba(59, 130, 246, 0.7); }
.fb-m { background: var(--green); }
.fn-meta { margin-left: auto; display: flex; align-items: center; gap: 10px; font-size: 11px; color: var(--muted); min-width: 0; }
.fn-expr { color: #6ee7b7; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 280px; }
.scr-wrap { max-height: 780px; flex: 1; }
.scr-tbl { min-width: 960px; }
.scr-tbl td { padding-top: 7px; padding-bottom: 7px; }
.score-cell { display: flex; align-items: center; gap: 7px; }
.score-bar { width: 58px; height: 6px; border-radius: 3px; background: var(--subtle); overflow: hidden; flex: none; }
.score-bar span { display: block; height: 100%; border-radius: 3px; background: linear-gradient(90deg, #2563eb, #10b981); }
.score-num { font-size: 12px; font-weight: 700; color: var(--text); min-width: 22px; }
.mini-spark { width: 90px; height: 26px; display: block; margin: 0 auto; }
.sigs { display: flex; flex-wrap: wrap; gap: 3px; max-width: 360px; white-space: normal; }
.sig {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 6px 1px 2px;
  font-size: 10.5px;
  line-height: 16px;
  border-radius: 4px;
  color: #5b6477;
  background: rgba(30, 37, 51, 0.6);
  border: 1px solid transparent;
  cursor: help;
  white-space: nowrap;
}
.sig b {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 3px;
  font-family: var(--mono);
  font-size: 9.5px;
  background: #1e2533;
  color: #5b6477;
}
.sig.ok { color: #6ee7b7; background: rgba(16, 185, 129, 0.1); border-color: rgba(16, 185, 129, 0.3); }
.sig.ok b { background: var(--green); color: #06251b; }

/* 스캔 애니메이션 */
.scan { position: relative; display: flex; flex-direction: column; gap: 18px; padding: 26px 22px; flex: 1; }
.scan-top { display: flex; align-items: center; gap: 26px; flex-wrap: wrap; }
.radar {
  position: relative;
  width: 132px;
  height: 132px;
  flex: none;
  border-radius: 50%;
  overflow: hidden;
  border: 1px solid rgba(16, 185, 129, 0.35);
  background:
    radial-gradient(circle, rgba(16, 185, 129, 0.1) 0%, transparent 70%),
    repeating-radial-gradient(circle, transparent 0 21px, rgba(16, 185, 129, 0.18) 21px 22px);
}
.radar::before, .radar::after { content: ''; position: absolute; background: rgba(16, 185, 129, 0.18); }
.radar::before { left: 50%; top: 0; bottom: 0; width: 1px; }
.radar::after { top: 50%; left: 0; right: 0; height: 1px; }
.radar-sweep {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  background: conic-gradient(from 0deg, transparent 0deg, rgba(16, 185, 129, 0) 280deg, rgba(16, 185, 129, 0.55) 360deg);
  animation: spin 2.4s linear infinite;
}
.radar-core { position: absolute; left: 50%; top: 50%; width: 8px; height: 8px; margin: -4px 0 0 -4px; border-radius: 50%; background: var(--green); box-shadow: 0 0 12px var(--green); }
.blip { position: absolute; width: 6px; height: 6px; border-radius: 50%; background: #34d399; box-shadow: 0 0 10px #34d399; opacity: 0; animation: blip 2.4s ease-out infinite; }
.b1 { top: 24%; left: 62%; animation-delay: 0.2s; }
.b2 { top: 64%; left: 72%; animation-delay: 0.9s; }
.b3 { top: 70%; left: 30%; animation-delay: 1.5s; }
.b4 { top: 32%; left: 26%; animation-delay: 2.0s; }
.scan-info { display: flex; flex-direction: column; gap: 8px; flex: 1; min-width: 240px; }
.scan-title { font-size: 15px; font-weight: 700; color: var(--text); display: flex; align-items: center; }
.dots { display: inline-flex; gap: 3px; margin-left: 6px; }
.dots i { width: 4px; height: 4px; border-radius: 50%; background: var(--green); animation: blink 1.2s ease-in-out infinite; }
.dots i:nth-child(2) { animation-delay: 0.2s; }
.dots i:nth-child(3) { animation-delay: 0.4s; }
.scan-sub { font-size: 12px; color: var(--muted); display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; }
.scan-expr { color: #6ee7b7; padding: 1px 7px; border-radius: 4px; background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.25); }
.scan-phases { display: flex; flex-direction: column; gap: 5px; font-size: 12px; color: #4b5568; }
.scan-phases li { display: flex; align-items: center; gap: 8px; transition: color 0.3s; }
.ph-mark { width: 12px; height: 12px; flex: none; border-radius: 50%; border: 1.5px solid #3a4256; position: relative; }
.scan-phases li.done { color: var(--text-2); }
.scan-phases li.done .ph-mark { border-color: var(--green); background: var(--green); }
.scan-phases li.done .ph-mark::after {
  content: '';
  position: absolute;
  left: 3.5px;
  top: 1px;
  width: 3px;
  height: 6px;
  border: solid #06251b;
  border-width: 0 1.5px 1.5px 0;
  transform: rotate(45deg);
}
.scan-phases li.active { color: var(--text); font-weight: 600; }
.scan-phases li.active .ph-mark { border-color: var(--amber); box-shadow: 0 0 0 3px rgba(245, 158, 11, 0.18); animation: pulse-amber 1.2s ease-out infinite; }
.prog { height: 5px; border-radius: 3px; background: var(--subtle); overflow: hidden; max-width: 460px; }
.prog.thin { height: 3px; margin-top: 6px; max-width: none; }
.prog-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  background: linear-gradient(90deg, #2563eb, #10b981);
  background-size: 200% 100%;
  animation: flow 1.6s linear infinite;
  transition: width 0.25s linear;
}
.prog-fill.green { background: linear-gradient(90deg, #10b981, #34d399, #10b981); background-size: 200% 100%; }
.scan-elapsed { font-size: 11px; color: var(--muted); }
.scan-rows { position: relative; display: flex; flex-direction: column; gap: 7px; overflow: hidden; border-radius: 6px; }
.scan-row .sk { height: 30px; opacity: 0.7; }
.scan-beam {
  position: absolute;
  left: 0;
  right: 0;
  height: 40px;
  top: -40px;
  background: linear-gradient(180deg, transparent, rgba(16, 185, 129, 0.16), transparent);
  border-bottom: 1px solid rgba(16, 185, 129, 0.45);
  animation: beam 2.2s ease-in-out infinite;
}

/* ═════════════ ROW 4 : AI 추천 ═════════════ */
.rec-presets {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--subtle);
}
.rp-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 2px 4px 2px 9px;
  font-size: 11.5px;
  color: var(--text-2);
  border-radius: 5px;
  border: 1px solid var(--border);
  background: var(--panel-2);
}
.rp-chip b { padding: 0 6px; border-radius: 3px; font-size: 11px; color: #6ee7b7; background: rgba(16, 185, 129, 0.12); }
.rp-chip.err b { color: #fca5a5; background: rgba(239, 68, 68, 0.12); }
.rec-loading { display: flex; flex-direction: column; }
.rec-loader { display: flex; align-items: center; gap: 14px; padding: 14px; border-bottom: 1px solid var(--subtle); }
.rec-loader-text { display: flex; flex-direction: column; gap: 3px; flex: 1; min-width: 0; font-size: 12.5px; }
.eq { display: flex; align-items: flex-end; gap: 3px; height: 26px; flex: none; }
.eq i { width: 4px; border-radius: 2px; background: var(--green); animation: eq 1s ease-in-out infinite; }
.eq i:nth-child(1) { animation-delay: 0s; }
.eq i:nth-child(2) { animation-delay: 0.15s; }
.eq i:nth-child(3) { animation-delay: 0.3s; }
.eq i:nth-child(4) { animation-delay: 0.45s; }
.eq i:nth-child(5) { animation-delay: 0.6s; }
.rec-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(262px, 1fr));
  gap: 10px;
  padding: 12px 14px 14px;
}
.rec-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  border-radius: 8px;
  background: var(--panel-2);
  border: 1px solid var(--subtle);
  min-width: 0;
  transition: border-color 0.15s, transform 0.15s, box-shadow 0.15s;
}
.rec-card:not(.sk-card):hover { border-color: #334056; transform: translateY(-1px); box-shadow: 0 8px 22px rgba(0, 0, 0, 0.3); }
.sk-card { gap: 10px; }
.rc-top { display: flex; align-items: flex-start; gap: 8px; }
.rc-rank { font-size: 11px; font-weight: 700; color: var(--amber); padding-top: 2px; }
.rc-name { display: flex; flex-direction: column; min-width: 0; flex: 1; line-height: 1.3; }
.rc-name b { font-size: 14px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rc-name span { font-size: 10.5px; }
.rc-score {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 3px 8px;
  border-radius: 6px;
  background: rgba(16, 185, 129, 0.1);
  border: 1px solid rgba(16, 185, 129, 0.3);
  line-height: 1.1;
}
.rc-score span { font-size: 15px; font-weight: 700; color: #6ee7b7; }
.rc-score small { font-size: 9px; color: var(--muted); }
.rc-quote { display: flex; align-items: center; gap: 8px; }
.rc-price { font-size: 18px; font-weight: 700; }
.rc-spark { width: 100%; height: 48px; display: block; }
.rc-spark.empty { display: flex; align-items: center; justify-content: center; border: 1px dashed var(--subtle); border-radius: 5px; }
.rc-metrics { display: flex; justify-content: space-between; gap: 6px; font-size: 11px; color: var(--muted); padding: 6px 0; border-top: 1px solid var(--subtle); border-bottom: 1px solid var(--subtle); }
.rc-metrics b { font-weight: 600; color: var(--text-2); margin-left: 2px; }
.rc-metrics b.up { color: var(--up); }
.rc-metrics b.down { color: var(--down); }
.rc-badges, .rc-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.badge-blue {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 4px;
  color: #bfdbfe;
  background: rgba(59, 130, 246, 0.14);
  border: 1px solid rgba(59, 130, 246, 0.3);
}
.tag-green {
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: 4px;
  color: #6ee7b7;
  background: rgba(16, 185, 129, 0.1);
}
.rc-reason { font-size: 11.5px; color: var(--muted); line-height: 1.5; }

/* ═════════════ ROW 5 : 뉴스 · 일정 ═════════════ */
.news-body { max-height: 520px; overflow-y: auto; scrollbar-width: thin; scrollbar-color: var(--border) transparent; }
.news-list { display: flex; flex-direction: column; }
.news-item {
  display: flex;
  gap: 10px;
  padding: 9px 14px;
  border-bottom: 1px solid rgba(30, 37, 51, 0.75);
  transition: background 0.12s;
}
.news-item:hover { background: #141b25; }
.news-idx { font-size: 11px; color: #4b5568; padding-top: 2px; flex: none; }
.news-main { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.news-title {
  color: var(--text);
  text-decoration: none;
  font-size: 13px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: keep-all;
  overflow-wrap: anywhere;
}
.news-title:hover { color: #93c5fd; }
.news-meta { display: flex; gap: 8px; font-size: 11px; color: var(--muted); }
.news-meta .press { color: #94a3b8; }

.cal-body { display: flex; flex-direction: column; max-height: 580px; overflow-y: auto; scrollbar-width: thin; scrollbar-color: var(--border) transparent; }
.cal-list { display: flex; flex-direction: column; }
.cal-day { display: grid; grid-template-columns: 92px minmax(0, 1fr); border-bottom: 1px solid rgba(30, 37, 51, 0.75); }
.cal-day.today { background: rgba(245, 158, 11, 0.05); }
.cal-date { display: flex; flex-direction: column; align-items: flex-start; gap: 3px; padding: 9px 12px 9px 14px; border-right: 1px solid var(--subtle); }
.cd-md { font-size: 14px; font-weight: 700; color: var(--text); line-height: 1.1; }
.cd-wd { font-size: 11px; color: var(--muted); }
.dday { font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: 3px; color: var(--muted); background: var(--subtle); }
.dday.soon { color: #fcd34d; background: rgba(245, 158, 11, 0.12); }
.dday.now { color: #1a1204; background: var(--amber); box-shadow: 0 0 10px rgba(245, 158, 11, 0.4); }
.cal-events { display: flex; flex-direction: column; gap: 5px; padding: 8px 12px; min-width: 0; }
.cal-ev { display: flex; align-items: flex-start; gap: 7px; font-size: 12px; line-height: 1.45; min-width: 0; }
.flag { flex: none; font-size: 13px; line-height: 1.3; }
.stars { flex: none; font-size: 10px; letter-spacing: -1px; line-height: 1.7; }
.stars .on { color: var(--amber); }
.stars .off { color: #2a3041; }
.ev-title { color: var(--text-2); min-width: 0; flex: 1; word-break: keep-all; overflow-wrap: anywhere; }
.cat-chip { flex: none; font-size: 10px; padding: 1px 6px; border-radius: 3px; border: 1px solid; white-space: nowrap; }
.cat-chip.amber { color: #fcd34d; border-color: rgba(245, 158, 11, 0.4); }
.cat-chip.blue { color: #93c5fd; border-color: rgba(59, 130, 246, 0.4); }
.cat-chip.green { color: #6ee7b7; border-color: rgba(16, 185, 129, 0.4); }
.cat-chip.violet { color: #c4b5fd; border-color: rgba(167, 139, 250, 0.4); }
.cat-chip.muted { color: var(--muted); border-color: var(--border); }
.cal-note { padding: 10px 14px 12px; font-size: 10.5px; color: #64748b; line-height: 1.5; }

/* ═════════════ 푸터 ═════════════ */
.foot {
  max-width: 1560px;
  margin: 0 auto;
  padding: 16px 20px 28px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  border-top: 1px solid var(--subtle);
  font-size: 11.5px;
  color: var(--muted);
}
.foot-src { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; color: var(--text-2); }
.foot-disc { line-height: 1.6; max-width: 980px; }
.foot-brand { font-size: 10.5px; color: #4b5568; }

/* ═════════════ 애니메이션 ═════════════ */
@keyframes tape { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes shimmer { 0% { background-position: 100% 50%; } 100% { background-position: 0 50%; } }
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
  70% { box-shadow: 0 0 0 7px rgba(239, 68, 68, 0); }
  100% { box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
}
@keyframes pulse-amber {
  0% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.6); }
  100% { box-shadow: 0 0 0 7px rgba(245, 158, 11, 0); }
}
@keyframes blink { 0%, 100% { opacity: 0.25; } 50% { opacity: 1; } }
@keyframes blip {
  0%, 100% { opacity: 0; transform: scale(0.4); }
  12% { opacity: 1; transform: scale(1.2); }
  55% { opacity: 0; transform: scale(1); }
}
@keyframes flow { from { background-position: 0 0; } to { background-position: 200% 0; } }
@keyframes beam { 0% { top: -40px; } 100% { top: 100%; } }
@keyframes eq { 0%, 100% { height: 6px; opacity: 0.6; } 50% { height: 26px; opacity: 1; } }

/* ═════════════ 반응형 ═════════════ */
@media (max-width: 1280px) {
  .row-1 { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .global-panel { grid-column: 1 / -1; }
  .clock-date { display: none; }
}
@media (max-width: 1100px) {
  .row-2, .row-5 { grid-template-columns: minmax(0, 1fr); }
  .scr-body { grid-template-columns: minmax(0, 1fr); }
  .scr-builder { border-right: 0; border-bottom: 1px solid var(--subtle); }
  .preset-list { max-height: 196px; }
  .hd-desc { flex-basis: 100%; order: 5; }
}
@media (max-width: 860px) {
  .topbar { flex-wrap: wrap; gap: 8px 12px; }
  .tb-left { flex: 1 1 100%; justify-content: space-between; gap: 12px; }
  .tb-right { flex: 1 1 100%; justify-content: flex-end; }
  .cr-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .topbar { padding: 8px 12px; }
  .tb-left { flex-direction: column; align-items: stretch; gap: 8px; }
  .tb-nav { overflow-x: auto; scrollbar-width: none; margin: 0 -4px; }
  .tb-nav::-webkit-scrollbar { display: none; }
  .nav-btn { padding: 6px 10px; font-size: 12px; }
  .tb-right { justify-content: space-between; gap: 8px; }
  .upd { display: none; }
  .clock-time { font-size: 13px; }
  .brand-sub { display: none; }

  .main { padding: 10px 10px 18px; gap: 10px; }
  .row { gap: 10px; }
  .row-1 { grid-template-columns: minmax(0, 1fr); }
  .panel-hd { padding: 8px 12px; }
  .idx-body { padding: 10px 12px 12px; }
  .idx-price { font-size: 26px; }
  .idx-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .idx-stats > div + div { border-left: 0; }
  .idx-stats > div:nth-child(even) { border-left: 1px solid var(--subtle); }
  .idx-stats > div:nth-child(n + 3) { border-top: 1px solid var(--subtle); }
  .gl-body { padding: 10px 12px 12px; }
  .gl-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .gl-spark { width: 48px; }
  .gl-price { font-size: 12px; }

  .cr-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .cr-stat b { font-size: 15px; }
  .movers-wrap, .crypto-wrap { max-height: 440px; }

  .blk { padding: 10px 12px; }
  .blk-row { flex-direction: column; }
  .num-in { width: 64px; }
  .run-btn { margin: 10px 12px 12px; }
  .scan { padding: 18px 14px; }
  .scan-top { gap: 16px; }
  .radar { width: 96px; height: 96px; }
  .scan-info { min-width: 0; }
  .fn-meta { margin-left: 0; flex-basis: 100%; }
  .fn-expr { max-width: 100%; }
  .scr-wrap { max-height: 620px; }

  .rec-grid { grid-template-columns: minmax(0, 1fr); padding: 10px 12px 12px; }
  .cal-day { grid-template-columns: 74px minmax(0, 1fr); }
  .cal-date { padding: 8px 8px 8px 12px; }
  .foot { padding: 14px 12px 22px; }
}
@media (prefers-reduced-motion: reduce) {
  .tape { overflow-x: auto; -webkit-mask-image: none; mask-image: none; }
  .tape-track { animation: none; }
  .sk, .radar-sweep, .blip, .scan-beam, .prog-fill, .eq i, .status-dot, .dots i, .spin, .btn-spinner { animation: none !important; }
}
</style>
