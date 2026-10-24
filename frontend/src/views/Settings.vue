<template>
  <div class="set">
    <!-- 상단 바 -->
    <header class="topbar">
      <div class="tb-left">
        <div class="brand" role="button" tabindex="0" @click="router.push('/')" @keydown.enter="router.push('/')">
          <svg class="logo" viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
            <defs>
              <linearGradient id="set-logo-grad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stop-color="#2563eb" /><stop offset="100%" stop-color="#10b981" />
              </linearGradient>
            </defs>
            <rect x="1" y="1" width="30" height="30" rx="8" fill="url(#set-logo-grad)" />
            <polyline points="7,22 12.5,16 16.5,19.5 24.5,10.5" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
            <polyline points="19.8,10.5 24.5,10.5 24.5,15.2" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <div class="brand-text">
            <span class="wordmark">WeAid<b>Stock</b></span>
            <span class="brand-sub">설정 · 로컬 전용</span>
          </div>
        </div>
        <nav class="tb-nav" aria-label="주요 메뉴">
          <button class="nav-btn" @click="router.push('/')">대시보드</button>
          <button class="nav-btn" @click="router.push('/stock')">5단계 AI 분석</button>
          <button class="nav-btn" @click="router.push('/reports')">보관함</button>
          <button class="nav-btn" @click="router.push('/watchlist')">관심종목</button>
          <button class="nav-btn" @click="router.push('/performance')">성과</button>
          <button class="nav-btn" @click="router.push('/ontology')">온톨로지</button>
          <button class="nav-btn" @click="router.push('/lab')">시뮬레이션 랩</button>
          <button class="nav-btn active" aria-current="page">설정</button>
        </nav>
      </div>
      <div class="tb-right">
        <span class="pill" :class="status ? 'ok' : 'warn'"><i></i>{{ status ? `백엔드 PID ${status.pid} · 가동 ${fmtUptime(status.uptime_sec)}` : '백엔드 확인 중' }}</span>
      </div>
    </header>

    <main class="wrap">
      <div class="intro">
        <div>
          <h1>서비스 설정</h1>
          <p class="muted">복잡한 값은 숨기고, 지금 필요한 작업만 안내합니다. 비밀 값은 이 PC에만 안전하게 저장됩니다.</p>
        </div>
      </div>

      <div v-if="restartNeeded && restart.state !== 'running'" class="banner warn">
        <b>재시작이 필요합니다.</b> LLM 공급자·모델·xAI 키처럼 시작할 때 읽는 값을 바꿨습니다.
        <button class="btn sm primary" @click="doRestart">지금 재시작</button>
      </div>
      <div v-if="restart.state === 'done'" class="banner ok">재시작 완료 — 새 설정이 적용되었습니다 (PID {{ status?.pid }}).</div>
      <div v-if="restart.state === 'fail'" class="banner err">{{ restart.error }}</div>
      <div v-if="loadError" class="banner err">{{ loadError }} <button class="btn sm" @click="load">다시 시도</button></div>

      <section class="setup-card" :class="{ ready: allReady }">
        <div class="setup-copy">
          <span class="eyebrow">{{ allReady ? '모든 기능 준비 완료' : '설정 도우미' }}</span>
          <h2>{{ setupTitle }}</h2>
          <p>{{ setupDescription }}</p>
          <button v-if="!coreReady" class="btn primary setup-action" @click="openCoreSettings">AI 분석 연결하기</button>
          <button v-else-if="!remoteReady" class="btn primary setup-action" @click="openRemoteSettings">원격 접속 연결하기</button>
          <a v-else class="btn primary setup-action" href="/" @click.prevent="router.push('/')">대시보드로 이동</a>
        </div>
        <div class="readiness" aria-label="서비스 준비 상태">
          <button class="ready-item" @click="openCoreSettings">
            <span class="ready-icon" :class="{ on: coreReady }">{{ coreReady ? '✓' : '1' }}</span>
            <span><b>AI 분석</b><small>{{ coreReady ? '연결됨' : '연결 필요' }}</small></span>
          </button>
          <button class="ready-item" @click="openRemoteSettings">
            <span class="ready-icon" :class="{ on: remoteReady }">{{ remoteReady ? '✓' : '2' }}</span>
            <span><b>원격 접속</b><small>{{ remoteReady ? '정상' : '선택 사항' }}</small></span>
          </button>
          <span class="ready-item static">
            <span class="ready-icon" :class="{ on: !!status }">{{ status ? '✓' : '3' }}</span>
            <span><b>로컬 서비스</b><small>{{ status ? '정상 작동' : '확인 중' }}</small></span>
          </span>
        </div>
      </section>

      <div class="grid">
        <!-- 설정 그룹 -->
        <section class="col-main">
          <details ref="coreSettings" class="section-panel" :open="!coreReady">
            <summary>
              <span><b>연결 및 기능 설정</b><small>AI 공급자, 데이터 소스, 알림과 자동 실행</small></span>
              <span class="summary-state">{{ configuredCount }}/{{ items.length }} 설정</span>
            </summary>
            <div class="section-body">
              <div v-for="g in groupOrder" :key="g" v-show="byGroup[g]?.length" class="card" :data-group="g">
                <div class="card-head">
                  <h2>{{ groups[g] || g }}</h2>
                  <span class="muted small">{{ byGroup[g]?.filter(i => i.set).length || 0 }}/{{ byGroup[g]?.length || 0 }} 설정됨</span>
                </div>
                <div v-for="it in byGroup[g]" :key="it.key" class="row" :class="{ dirty: isDirty(it.key) }">
                  <div class="row-label">
                    <div class="lbl">
                      {{ it.label }}
                      <span v-if="it.restart" class="tag" title="바꾸면 재시작해야 적용됩니다">재시작</span>
                    </div>
                    <div class="key mono">{{ it.key }}</div>
                    <div class="help" v-if="it.help">{{ it.help }}</div>
                  </div>
                  <div class="row-ctl">
                    <div class="cur">
                      <span v-if="clears.has(it.key)" class="state del">삭제 예정</span>
                      <span v-else-if="it.set" class="state on">
                        <i></i>{{ it.secret ? '설정됨' : '현재' }} <span class="mono">{{ it.display }}</span>
                      </span>
                      <span v-else class="state off">미설정</span>
                      <span v-if="it.pending_restart" class="state pend">재시작 대기</span>
                    </div>
                    <div class="inputs">
                      <select v-if="it.options" v-model="drafts[it.key]" :aria-label="it.label">
                        <option value="">— 변경 없음 —</option>
                        <option v-for="o in it.options" :key="o" :value="o">{{ o }}</option>
                      </select>
                      <div v-else class="pw">
                        <input :type="it.secret && !reveal[it.key] ? 'password' : 'text'" v-model="drafts[it.key]"
                          :placeholder="it.set ? '새 값을 입력하면 교체' : (it.placeholder || '')" autocomplete="off" spellcheck="false"
                          :aria-label="it.label" />
                        <button v-if="it.secret" class="eye" type="button" :title="reveal[it.key] ? '숨기기' : '보기'" @click="reveal[it.key] = !reveal[it.key]">
                          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></svg>
                        </button>
                      </div>
                      <button v-if="it.test" class="btn sm" :disabled="testing[it.key]" @click="runTest(it)">{{ testing[it.key] ? '확인 중…' : '연결 테스트' }}</button>
                      <button v-if="it.set && !clears.has(it.key)" class="btn sm ghost" title="값 삭제" @click="toggleClear(it.key)">삭제</button>
                      <button v-else-if="clears.has(it.key)" class="btn sm ghost" @click="toggleClear(it.key)">취소</button>
                    </div>
                    <div v-if="tests[it.key]" class="test" :class="tests[it.key].ok ? 'ok' : 'err'">
                      {{ tests[it.key].ok ? '✓' : '✕' }} {{ tests[it.key].message }} <span class="muted">· {{ tests[it.key].ms }}ms</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </details>

          <!-- 운영 지표 -->
          <details class="section-panel">
            <summary>
              <span><b>운영 현황과 진단</b><small>사용량, 예상 비용, 처리 시간과 자체 점검</small></span>
              <span class="summary-state">고급</span>
            </summary>
            <div class="section-body"><div class="card ops">
            <div class="card-head">
              <h2>운영 지표 <span class="muted small">LLM 사용량·추정 비용·단계별 소요 시간</span></h2>
              <button class="btn sm" :disabled="selftest?.state === 'running'" @click="runSelftest">{{ selftest?.state === 'running' ? '점검 중…' : '자체 점검' }}</button>
              <div class="seg">
                <button v-for="dd in [1, 7, 30]" :key="dd" :class="{ on: opsDays === dd }" @click="loadOps(dd)">{{ dd === 1 ? '오늘·24시간' : `${dd}일` }}</button>
              </div>
            </div>
            <div v-if="selftest && selftest.state" class="selftest">
              <div class="st-head">
                <span class="state" :class="selftest.state === 'running' ? '' : selftest.ok ? 'on' : 'off'"><i></i>
                  {{ selftest.state === 'running' ? '회귀 테스트 실행 중…' : selftest.ok ? `회귀 테스트 ${selftest.ran}개 모두 통과` : `회귀 테스트 실패 ${selftest.failed?.length || ''}` }}
                </span>
                <span v-if="selftest.sec" class="muted small">{{ selftest.sec }}초</span>
              </div>
              <ul class="live">
                <li v-for="c in selftest.live || []" :key="c.name" :class="c.ok ? 'ok' : 'bad'"><b>{{ c.ok ? '✓' : '✕' }} {{ c.name }}</b> <span class="muted">{{ c.detail }}</span></li>
              </ul>
              <div v-if="selftest.failed?.length" class="small bad-t">실패: {{ selftest.failed.join(', ') }}</div>
              <div v-if="selftest.error" class="small bad-t">{{ selftest.error }}</div>
              <details v-if="selftest.tail && !selftest.ok"><summary class="small">출력 보기</summary><pre class="st-tail">{{ selftest.tail }}</pre></details>
            </div>
            <div v-if="!ops" class="muted small">{{ opsError || '불러오는 중…' }}</div>
            <template v-else>
              <div v-if="!ops.installed?.llm" class="banner err small">LLM 호출 계측이 꺼져 있거나 설치되지 않았습니다 (OPS_METRICS).</div>
              <div class="ops-kpis">
                <div class="okpi"><span>LLM 호출</span><b>{{ n(ops.total.calls) }}</b><small>오류 {{ n(ops.total.errors) }}{{ ops.total.error_rate != null ? ` (${ops.total.error_rate}%)` : '' }}</small></div>
                <div class="okpi"><span>입력 토큰</span><b>{{ kTok(ops.total.prompt_tokens) }}</b><small>출력 {{ kTok(ops.total.completion_tokens) }} · 추론 {{ kTok(ops.total.reasoning_tokens) }}</small></div>
                <div class="okpi"><span>추정 비용</span><b>{{ n(ops.total.cost_krw) }}원</b><small>${{ ops.total.cost_usd.toFixed(2) }} · 환율 {{ n(ops.usd_krw) }}</small></div>
                <div class="okpi"><span>응답 시간</span><b>{{ ops.total.sec_p50 ?? '-' }}초</b><small>90% {{ ops.total.sec_p90 ?? '-' }}초 이내</small></div>
              </div>
              <p v-if="ops.total.estimated_share || ops.total.unpriced" class="muted small">
                <template v-if="ops.total.estimated_share">토큰 수를 알려 주지 않은 호출 {{ ops.total.estimated_share }}% 는 글자 수로 추정했습니다. </template>
                <template v-if="ops.total.unpriced">단가를 모르는 모델 호출 {{ ops.total.unpriced }}건은 비용에서 빠졌습니다 (LLM_PRICES 에 추가).</template>
              </p>

              <div v-if="ops.daily?.length > 1" class="ops-bars" role="img" :aria-label="`일별 추정 비용, 최근 ${ops.daily.length}일`">
                <div v-for="dy in ops.daily" :key="dy.date" class="bar-col" :title="`${dy.date} · 호출 ${dy.calls} · 토큰 ${kTok(dy.tokens)} · $${dy.cost_usd.toFixed(3)} (${n(Math.round(dy.cost_usd * ops.usd_krw))}원)`">
                  <i :style="{ height: `${maxCost ? Math.max(dy.cost_usd > 0 ? 3 : 0, (dy.cost_usd / maxCost) * 100) : 0}%` }"></i>
                  <span>{{ dy.date.slice(8) }}</span>
                </div>
              </div>

              <div class="ops-tables">
                <div>
                  <h3>단계별 소요 시간</h3>
                  <table class="otable">
                    <thead><tr><th>단계</th><th class="r">실행</th><th class="r">중앙값</th><th class="r">90%</th><th class="r">실패</th></tr></thead>
                    <tbody>
                      <tr v-for="r in ops.stages" :key="r.stage"><td>{{ r.label }}</td><td class="r">{{ r.runs }}</td><td class="r">{{ secTxt(r.sec_p50) }}</td><td class="r">{{ secTxt(r.sec_p90) }}</td><td class="r" :class="{ bad: r.failed }">{{ r.failed }}</td></tr>
                      <tr v-if="!ops.stages.length"><td colspan="5" class="muted">기록 없음 — 분석을 한 번 돌리면 쌓입니다</td></tr>
                    </tbody>
                  </table>
                </div>
                <div>
                  <h3>단계별 LLM 사용</h3>
                  <table class="otable">
                    <thead><tr><th>단계</th><th class="r">호출</th><th class="r">토큰</th><th class="r">비용</th></tr></thead>
                    <tbody>
                      <tr v-for="r in ops.by_stage" :key="r.stage"><td>{{ r.label }}</td><td class="r">{{ r.calls }}</td><td class="r">{{ kTok(r.prompt_tokens + r.completion_tokens + r.reasoning_tokens) }}</td><td class="r">{{ n(r.cost_krw) }}원</td></tr>
                      <tr v-if="!ops.by_stage.length"><td colspan="4" class="muted">기록 없음</td></tr>
                    </tbody>
                  </table>
                  <h3>모델별</h3>
                  <table class="otable">
                    <thead><tr><th>모델</th><th class="r">호출</th><th class="r">단가 (입/출)</th><th class="r">비용</th></tr></thead>
                    <tbody>
                      <tr v-for="r in ops.by_model" :key="r.model"><td class="mono">{{ r.model }}</td><td class="r">{{ r.calls }}</td><td class="r">{{ r.price ? `$${r.price[0]} / $${r.price[1]}` : '미지정' }}</td><td class="r">{{ n(r.cost_krw) }}원</td></tr>
                    </tbody>
                  </table>
                </div>
              </div>
              <details v-if="ops.recent_llm_errors?.length || ops.recent_stage_failures?.length" class="ops-err">
                <summary class="small">최근 실패 {{ (ops.recent_llm_errors?.length || 0) + (ops.recent_stage_failures?.length || 0) }}건</summary>
                <div v-for="(e, i) in ops.recent_stage_failures" :key="'s' + i" class="small"><span class="mono muted">{{ e.at.replace('T', ' ') }}</span> [{{ e.stage }}] {{ e.key }} — {{ e.error }}</div>
                <div v-for="(e, i) in ops.recent_llm_errors" :key="'l' + i" class="small"><span class="mono muted">{{ e.at.replace('T', ' ') }}</span> LLM {{ e.model }} ({{ e.stage }}) — {{ e.error }}</div>
              </details>
            </template>
          </div>
            </div>
          </details>
        </section>

        <!-- 시스템 -->
        <aside class="col-side">
          <div class="card remote">
            <div class="card-head">
              <h2>원격 접속</h2>
              <button class="btn sm ghost" :disabled="remoteChecking" @click="loadRemote(true)">{{ remoteChecking ? '확인 중…' : '연결 확인' }}</button>
            </div>
            <a v-if="rs" class="pub mono" :href="rs.public_url" target="_blank" rel="noopener">{{ pubHost }} ↗</a>
            <ol v-if="rs" class="rsteps">
              <li :class="rs.access?.certs_ok === false ? 'err' : (codeOk ? 'ok' : 'todo')">
                <i></i><b>접속 인증</b>
                <span>{{ authText }}</span>
              </li>
              <li :class="rs.build?.state === 'error' ? 'err' : (rs.dist?.exists && !rs.dist?.stale ? 'ok' : 'todo')">
                <i></i><b>화면 빌드</b>
                <span>{{ buildText }}</span>
                <button class="btn sm" :disabled="building" @click="doBuild">{{ building ? '빌드 중…' : (rs.dist?.exists ? '다시 빌드' : '빌드') }}</button>
              </li>
              <li :class="rs.tunnel?.token_set ? 'ok' : 'todo'">
                <i></i><b>터널 토큰</b>
                <span>{{ rs.tunnel?.token_set ? `터널 ${(rs.tunnel.token?.tunnel_id || '').slice(0, 8)}… 연결용` : '‘Cloudflare 터널 토큰’ 칸에 붙여 넣으세요' }}</span>
              </li>
              <li :class="rs.tunnel?.running ? 'ok' : (rs.tunnel?.download?.state === 'error' ? 'err' : 'todo')">
                <i></i><b>커넥터</b>
                <span>{{ connText }}</span>
                <button v-if="!rs.tunnel?.running" class="btn sm primary" :disabled="!rs.tunnel?.token_set || tunnelBusy" @click="doTunnel">{{ tunnelBtn }}</button>
                <button v-else-if="rs.tunnel?.our_pid" class="btn sm ghost" @click="doTunnelStop">끊기</button>
              </li>
              <li v-if="rs.tunnel?.public" :class="rs.tunnel.public.ok ? 'ok' : 'err'">
                <i></i><b>공개 주소</b>
                <span>{{ rs.tunnel.public.message }}<template v-if="rs.tunnel.public.ms"> · {{ rs.tunnel.public.ms }}ms</template></span>
              </li>
            </ol>
            <div v-else class="muted small">불러오는 중…</div>
            <p v-if="rs?.tunnel?.last_error && !rs?.tunnel?.running" class="rerr small">{{ rs.tunnel.last_error }}</p>
            <details class="how">
              <summary>직접 연결해야 할 때만 보기</summary>
              <ol>
                <li><a href="https://one.dash.cloudflare.com/" target="_blank" rel="noopener">Cloudflare Zero Trust 대시보드</a> → Networks → Tunnels → <b>weaidstock</b> → 구성(Configure)</li>
                <li>커넥터 설치 화면에서 Windows 를 고르고 명령어를 복사</li>
                <li>왼쪽 <b>Cloudflare 터널 토큰</b> 칸에 그대로 붙여 넣고 저장 → 여기서 <b>연결</b></li>
              </ol>
              <p class="muted small">그 명령을 관리자 PowerShell 에서 직접 실행하면 윈도우 서비스로 설치되어 PC 를 켤 때마다 자동 연결됩니다. 이때는 ‘터널 자동 연결’을 off 로 두세요. 원격에서는 이 설정 화면이 열리지 않습니다.</p>
              <p class="muted small">공개 주소는 Cloudflare Access 가 앞에서 막습니다 — 등록된 Gmail 로 받은 일회용 코드로 로그인하며 7일간 유지됩니다. 허용 이메일은 Zero Trust → Access → 정책 ‘WeAidStock - owner’ 에서 바꿉니다.</p>
            </details>
          </div>

          <details class="section-panel side-tools">
            <summary>
              <span><b>고급 관리 도구</b><small>시스템 정보, 구성요소 설치와 로그</small></span>
            </summary>
            <div class="section-body">
          <div class="card">
            <div class="card-head"><h2>시스템</h2><button class="btn sm ghost" @click="loadStatus">새로고침</button></div>
            <dl class="kv" v-if="status">
              <dt>LLM</dt><dd><span class="mono">{{ status.llm_model || '-' }}</span> <span class="muted">({{ status.llm_active === 'xai' ? 'xAI' : '사용자 지정' }})</span></dd>
              <dt>Python</dt><dd class="mono">{{ status.python }}</dd>
              <dt>시작</dt><dd class="mono">{{ (status.started_at || '').replace('T', ' ') }}</dd>
              <dt>진행 작업</dt>
              <dd>
                <span v-if="!status.running_tasks?.length" class="muted">없음</span>
                <div v-for="t in status.running_tasks" :key="t.task_id" class="task-mini"><b>{{ t.task_type }}</b> {{ t.progress }}% <span class="muted">{{ (t.message || '').slice(0, 40) }}</span></div>
              </dd>
              <dt>스케줄러</dt>
              <dd>
                <span v-if="status.scheduler" :class="status.scheduler.enabled ? 'up-ok' : 'muted'">
                  {{ status.scheduler.enabled ? `켜짐 · 평일 ${status.scheduler.time}` : '꺼짐' }}
                  <span v-if="status.scheduler.last_run" class="muted small"> · 최근 {{ status.scheduler.last_run }}</span>
                </span>
                <span v-else class="muted">준비 중</span>
              </dd>
            </dl>
            <div v-else class="muted small">불러오는 중…</div>
          </div>

          <div class="card">
            <div class="card-head"><h2>다국어 감성 모델</h2></div>
            <p class="muted small">뉴스·공시·커뮤니티 글의 긍정/부정을 이 PC 에서 직접 분류합니다 (<span class="mono">{{ status?.sentiment?.model || 'tabularisai/multilingual-sentiment-analysis' }}</span>, CPU 로 동작). 설치 전에는 Grok 이 대신 분류합니다.</p>
            <div class="sent-state" v-if="status">
              <span class="state" :class="status.sentiment.installed ? 'on' : 'off'"><i></i>PyTorch·transformers {{ status.sentiment.installed ? '설치됨' : '미설치' }}</span>
              <span class="state" :class="status.sentiment.cached ? 'on' : 'off'"><i></i>모델 {{ status.sentiment.cached ? '내려받음' : '없음' }}</span>
            </div>
            <p class="muted small">설치 용량: PyTorch CPU 약 200MB + 모델 약 540MB. 인터넷에서 내려받으며 5~15분 걸립니다.</p>
            <button class="btn primary sm" :disabled="installing || (status?.sentiment?.installed && status?.sentiment?.cached)" @click="doInstall">
              {{ status?.sentiment?.installed && status?.sentiment?.cached ? '설치 완료' : (installing ? '설치 중…' : '설치하기') }}
            </button>
          </div>

          <div class="card">
            <div class="card-head"><h2>선택 구성요소</h2></div>
            <p class="muted small">없어도 동작하지만 설치하면 기능이 늘어납니다. PyPI 에서 내려받아 이 PC 의 Python 에 설치합니다 (1~3분).</p>
            <div v-for="g in PKG_LIST" :key="g.key" class="pkg-row">
              <div>
                <b>{{ g.label }}</b>
                <div class="muted small">{{ g.desc }}</div>
                <span v-for="m in g.mods" :key="m" class="state" :class="status?.packages?.[m] ? 'on' : 'off'"><i></i>{{ m }}</span>
              </div>
              <button class="btn sm" :disabled="!!pkgBusy[g.key] || g.mods.every(m => status?.packages?.[m])" @click="doInstallPkg(g.key)">
                {{ g.mods.every(m => status?.packages?.[m]) ? '설치됨' : (pkgBusy[g.key] ? (pkgMsg[g.key] || '설치 중…') : '설치') }}
              </button>
            </div>
          </div>

          <div class="card logs-card">
            <div class="card-head">
              <h2>백엔드 로그</h2>
              <div class="seg">
                <button :class="{ on: logLevel === '' }" @click="setLevel('')">전체</button>
                <button :class="{ on: logLevel === 'WARNING' }" @click="setLevel('WARNING')">경고</button>
                <button :class="{ on: logLevel === 'ERROR' }" @click="setLevel('ERROR')">오류</button>
              </div>
            </div>
            <div class="logs mono" ref="logEl">
              <div v-if="!logs.length" class="muted">로그 없음</div>
              <div v-for="(ln, i) in logs" :key="i" :class="lineCls(ln)">{{ ln }}</div>
            </div>
            <label class="auto"><input type="checkbox" v-model="logAuto" /> 5초마다 자동 갱신</label>
          </div>
            </div>
          </details>
        </aside>
      </div>
    </main>

    <!-- 저장 바 -->
    <Transition name="bar">
      <div v-if="dirtyCount" class="savebar">
        <span>변경 {{ dirtyCount }}개 <span class="muted small">({{ dirtyKeys.join(', ') }})</span></span>
        <div class="sb-actions">
          <button class="btn ghost" @click="reset">되돌리기</button>
          <button class="btn primary" :disabled="saving" @click="save">{{ saving ? '저장 중…' : '저장' }}</button>
        </div>
      </div>
    </Transition>
    <Transition name="bar"><div v-if="toast" class="toast" :class="toast.kind">{{ toast.text }}</div></Transition>

    <ProgressModal
      :show="pm.show" title="감성 모델 설치 중"
      subtitle="PyTorch(CPU) → transformers → 모델 내려받기·테스트 순서로 진행합니다."
      done-title="감성 모델 설치 완료"
      :stages="pm.stages" :progress="pm.progress" :message="pm.message" :logs="pm.logs"
      :status="pm.status" :error="pm.error" :started-at="pm.startedAt" :expected-sec="600"
      @close="pmClose" @retry="pmClose(); doInstall()"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import ProgressModal from '../components/ProgressModal.vue'
import { useProgress } from '../composables/useProgress.js'
import { getSettings, saveSettings, testService, getAdminStatus, getLogs, restartBackend, installSentiment, installPackages, getOpsMetrics, startSelftest, getSelftest,
  getRemoteStatus, startTunnel, stopTunnel, buildWeb } from '../api/admin.js'

const router = useRouter()
const items = ref([])
const groups = ref({})
const envPath = ref('')
const status = ref(null)
const loadError = ref('')
const drafts = reactive({})
const reveal = reactive({})
const testing = reactive({})
const tests = reactive({})
const clears = reactive(new Set())
const saving = ref(false)
const restartNeeded = ref(false)
const restart = reactive({ state: 'idle', error: '' })
const toast = ref(null)
const logs = ref([])
const logLevel = ref('')
const logAuto = ref(false)
const logEl = ref(null)
const installing = ref(false)
const coreSettings = ref(null)
let logTimer = null
let toastTimer = null

const { pm, start: pmStart, log: pmLog, stage: pmStage, done: pmDone, fail: pmFail, close: pmClose, follow } = useProgress()

const groupOrder = ['llm', 'judge', 'data', 'sim', 'schedule', 'alerts', 'ops', 'remote', 'other']
const byGroup = computed(() => {
  const out = {}
  for (const it of items.value) (out[it.group] = out[it.group] || []).push(it)
  return out
})
const isDirty = (k) => (drafts[k] || '').trim() !== '' || clears.has(k)
const dirtyKeys = computed(() => items.value.map(i => i.key).filter(isDirty))
const dirtyCount = computed(() => dirtyKeys.value.length)
const configuredCount = computed(() => items.value.filter(i => i.set).length)
const itemSet = (key) => !!items.value.find(i => i.key === key)?.set
const coreReady = computed(() => {
  const provider = items.value.find(i => i.key === 'LLM_PROVIDER')?.display || 'xai'
  return provider === 'custom' ? itemSet('LLM_API_KEY') : itemSet('XAI_API_KEY')
})
const remoteReady = computed(() => !!rs.value?.tunnel?.running && rs.value?.tunnel?.public?.ok !== false)
const allReady = computed(() => coreReady.value && remoteReady.value && !!status.value)
const setupTitle = computed(() => {
  if (!coreReady.value) return 'AI 분석 연결이 필요합니다'
  if (!remoteReady.value) return 'AI 분석은 준비됐습니다'
  return '서비스가 정상 작동 중입니다'
})
const setupDescription = computed(() => {
  if (!coreReady.value) return '분석에 사용할 AI 공급자만 연결하면 나머지는 권장값으로 바로 시작할 수 있습니다.'
  if (!remoteReady.value) return '이 PC에서는 바로 사용할 수 있습니다. 외부 접속은 필요할 때 한 번만 연결하세요.'
  return 'AI 분석과 원격 접속이 모두 준비됐습니다. 별도 작업 없이 계속 사용할 수 있습니다.'
})

async function openSettingsGroup(group) {
  if (coreSettings.value) coreSettings.value.open = true
  await nextTick()
  document.querySelector(`[data-group="${group}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
const openCoreSettings = () => openSettingsGroup('llm')
const openRemoteSettings = () => openSettingsGroup('remote')

function flash(text, kind = 'ok') {
  toast.value = { text, kind }
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = null), 3500)
}
const errMsg = (e) => e?.response?.data?.error || e?.message || '요청 실패'

async function load() {
  loadError.value = ''
  try {
    const r = await getSettings()
    items.value = r.data.items
    groups.value = r.data.groups
    envPath.value = r.data.env_path
    for (const it of r.data.items) if (!(it.key in drafts)) drafts[it.key] = ''
    restartNeeded.value = r.data.items.some(i => i.pending_restart)
  } catch (e) {
    loadError.value = e?.response?.status === 403 ? '이 화면은 이 PC 에서만 열 수 있습니다.' : `설정을 불러오지 못했습니다: ${errMsg(e)}`
  }
}
async function loadStatus() {
  try { status.value = (await getAdminStatus()).data } catch (e) { status.value = null }
}
async function loadLogs() {
  try {
    const r = await getLogs(250, logLevel.value)
    logs.value = r.data.lines
    await nextTick()
    if (logEl.value) logEl.value.scrollTop = logEl.value.scrollHeight
  } catch (e) { /* 재시작 중일 수 있음 */ }
}
function setLevel(l) { logLevel.value = l; loadLogs() }
const lineCls = (ln) => (/ERROR|실패|오류/.test(ln) ? 'l-err' : /WARNING|재시도/.test(ln) ? 'l-warn' : '')

function toggleClear(k) {
  if (clears.has(k)) clears.delete(k)
  else { clears.add(k); drafts[k] = '' }
}
function reset() {
  for (const k of Object.keys(drafts)) drafts[k] = ''
  clears.clear()
}

async function save() {
  saving.value = true
  try {
    const values = {}
    for (const k of dirtyKeys.value) if ((drafts[k] || '').trim()) values[k] = drafts[k].trim()
    const r = await saveSettings(values, [...clears])
    const changed = r.data.changed || []
    if (r.data.restart_required) restartNeeded.value = true
    reset()
    await load()
    loadRemote(false)
    flash(changed.length ? `저장했습니다 — ${changed.join(', ')}` : '바뀐 값이 없습니다')
  } catch (e) {
    flash(`저장 실패: ${errMsg(e)}`, 'err')
  } finally { saving.value = false }
}

async function runTest(it) {
  testing[it.key] = true
  try {
    const v = (drafts[it.key] || '').trim()
    const r = await testService(it.test, v || null)
    tests[it.key] = r.data
  } catch (e) {
    tests[it.key] = { ok: false, message: errMsg(e), ms: 0 }
  } finally { testing[it.key] = false }
}

async function doRestart() {
  if (restart.state === 'running') return
  const oldPid = status.value?.pid
  restart.state = 'running'
  restart.error = ''
  try {
    await restartBackend()
  } catch (e) {
    restart.state = 'fail'
    restart.error = `재시작 요청 실패: ${errMsg(e)} — restart-backend.bat 을 직접 실행해 주세요.`
    return
  }
  const t0 = Date.now()
  await new Promise(r => setTimeout(r, 2500))
  while (Date.now() - t0 < 60000) {
    try {
      const s = (await getAdminStatus()).data
      if (s.pid && s.pid !== oldPid) {
        status.value = s
        restart.state = 'done'
        restartNeeded.value = false
        await load()
        loadLogs()
        setTimeout(() => { if (restart.state === 'done') restart.state = 'idle' }, 6000)
        return
      }
    } catch (e) { /* 내려가 있는 동안은 실패가 정상 */ }
    await new Promise(r => setTimeout(r, 1500))
  }
  restart.state = 'fail'
  restart.error = '60초 안에 백엔드가 다시 올라오지 않았습니다. restart.log 를 확인하거나 restart-backend.bat 을 직접 실행해 주세요.'
}

const INSTALL_STAGES = [
  { key: 'pip', label: 'pip 확인' },
  { key: 'torch', label: 'PyTorch (CPU)' },
  { key: 'transformers', label: 'transformers' },
  { key: 'model', label: '모델 내려받기·테스트' }
]
async function doInstall() {
  installing.value = true
  try {
    const r = await installSentiment()
    pmStart(INSTALL_STAGES.map(s => ({ ...s, status: 'pending' })))
    pmLog('설치 작업을 시작합니다')
    const order = INSTALL_STAGES.map(s => s.key)
    await follow(r.data.task_id, {
      interval: 1500,
      onUpdate: (t) => {
        const st = (t.progress_detail || {}).stage
        const idx = order.indexOf(st)
        if (idx >= 0) order.forEach((k, i) => pmStage(k, i < idx ? 'done' : i === idx ? 'running' : 'pending'))
      }
    })
    order.forEach(k => pmStage(k, 'done'))
    pmDone('감성 모델 설치 완료 — 다음 분석부터 자동으로 사용됩니다')
    await loadStatus()
  } catch (e) {
    const m = errMsg(e)
    if (pm.show) pmFail(m); else flash(m, 'err')
  } finally { installing.value = false }
}

// ── 운영 지표
const ops = ref(null)
const opsDays = ref(7)
const opsError = ref('')
const n = (v) => (v == null ? '-' : Number(v).toLocaleString('ko-KR'))
const kTok = (v) => (v == null ? '-' : v >= 1e6 ? `${(v / 1e6).toFixed(2)}M` : v >= 1e3 ? `${(v / 1e3).toFixed(1)}k` : String(v))
const secTxt = (v) => (v == null ? '-' : v >= 90 ? `${Math.floor(v / 60)}분 ${Math.round(v % 60)}초` : `${v}초`)
const maxCost = computed(() => Math.max(0, ...(ops.value?.daily || []).map(x => x.cost_usd)))
const selftest = ref(null)
async function runSelftest() {
  try {
    selftest.value = (await startSelftest()).data
    const poll = async () => {
      try { selftest.value = (await getSelftest()).data } catch (e) { /* 다음 주기에 다시 */ }
      if (selftest.value?.state === 'running') setTimeout(poll, 2000)
      else loadOps()
    }
    setTimeout(poll, 1500)
  } catch (e) { flash(errMsg(e), 'err') }
}
async function loadOps(days) {
  if (days) opsDays.value = days
  opsError.value = ''
  try { ops.value = (await getOpsMetrics(opsDays.value)).data } catch (e) { opsError.value = `운영 지표를 불러오지 못했습니다: ${errMsg(e)}` }
}

// ── 선택 구성요소 (rdflib·pyshacl·pykrx)
const PKG_LIST = [
  { key: 'ontology', label: '온톨로지 도구', mods: ['rdflib', 'pyshacl'], desc: '온톨로지 화면의 SPARQL 질의와 표준 SHACL 검증' },
  { key: 'krx', label: 'KRX 수급·공매도', mods: ['pykrx'], desc: '투자자별 순매수·공매도 잔고를 KRX 에서 직접 수집 (없으면 네이버 수급만 사용)' },
]
const pkgBusy = reactive({})
const pkgMsg = reactive({})
async function doInstallPkg(key) {
  pkgBusy[key] = true
  pkgMsg[key] = ''
  try {
    const r = await installPackages(key)
    await follow(r.data.task_id, { interval: 2000, onUpdate: (t) => { pkgMsg[key] = (t.message || '').replace(/^.*? 설치 — /, '').slice(0, 28) } })
    flash('설치 완료 — 바로 사용할 수 있습니다', 'ok')
    await loadStatus()
  } catch (e) {
    flash(errMsg(e), 'err')
  } finally { pkgBusy[key] = false }
}

// ── 원격 접속 (stock.webig.ai)
const rs = ref(null)
const remoteChecking = ref(false)
const tunnelBusy = ref(false)
let remoteTimer = null
const building = computed(() => rs.value?.build?.state === 'running')
const downloading = computed(() => rs.value?.tunnel?.download?.state === 'downloading')
const codeOk = computed(() => !!rs.value && (rs.value.mode === 'open' || (rs.value.mode === 'code' && rs.value.code_set)
  || (rs.value.mode === 'access' && !!rs.value.access?.team && rs.value.access?.aud?.length > 0)))
const authText = computed(() => {
  const r = rs.value
  if (!r) return ''
  if (r.mode === 'access') {
    const a = r.access || {}
    let t = `Cloudflare Access · Gmail 일회용 코드 로그인 (${a.team || '팀 미설정'})`
    if (a.emails?.length) t += ` · 앱 추가 확인 ${a.emails.join(', ')}`
    if (a.certs_ok === false) t += ` — 공개키 확인 실패: ${a.error || ''}`
    else if (a.certs_ok) t += ` · 공개키 ${a.keys}개 확인`
    return t
  }
  if (r.mode === 'open') return '로그인 없이 열림 (open) — 권장하지 않음'
  if (r.mode === 'off') return '원격 꺼짐 (off)'
  return r.code_set ? '접속 코드 설정됨 (code)' : '‘원격 접속 코드’ 칸에서 8자 이상으로 정하세요'
})
const pubHost = computed(() => (rs.value?.public_url || '').replace(/^https?:\/\//, ''))
const mb = (b) => (b / 1048576).toFixed(1)
const buildText = computed(() => {
  const r = rs.value
  if (!r) return ''
  if (r.build?.state === 'running') return `빌드 중… ${(r.build.tail || []).slice(-1)[0] || ''}`.slice(0, 90)
  if (r.build?.state === 'error') return `실패: ${r.build.error}`
  if (r.dist?.exists) return `${r.dist.built_at}${r.dist.stale ? ' · 화면 코드가 더 새로움 — 다시 빌드 권장' : ''}`
  return '아직 없음 — 공개 주소에 보일 화면을 만듭니다 (1~2분)'
})
const connText = computed(() => {
  const t = rs.value?.tunnel
  if (!t) return ''
  if (!t.platform_ok) return 'Windows 에서만 관리합니다'
  if (t.service === 'RUNNING') return '윈도우 서비스로 실행 중'
  if (t.pids?.length) return `실행 중 (PID ${t.pids.join(', ')})`
  if (t.download?.state === 'downloading') return `cloudflared 내려받는 중 ${mb(t.download.bytes || 0)}${t.download.total ? ' / ' + mb(t.download.total) : ''} MB`
  if (t.download?.state === 'error') return `내려받기 실패: ${t.download.error}`
  if (!t.exe) return '미설치 — 연결을 누르면 Cloudflare 공식 릴리스(GitHub)에서 내려받아 실행합니다'
  return '꺼짐'
})
const tunnelBtn = computed(() => downloading.value ? '내려받는 중…' : (rs.value?.tunnel?.exe ? '연결' : '내려받고 연결'))

async function loadRemote(check = false) {
  if (check) remoteChecking.value = true
  try { rs.value = (await getRemoteStatus(check)).data } catch (e) { /* 재시작 중일 수 있음 */ }
  finally { if (check) remoteChecking.value = false }
}
function pollRemote(untilFn, then) {
  clearInterval(remoteTimer)
  const t0 = Date.now()
  remoteTimer = setInterval(async () => {
    await loadRemote(false)
    if (untilFn() || Date.now() - t0 > 11 * 60 * 1000) {
      clearInterval(remoteTimer)
      remoteTimer = null
      if (then) then()
    }
  }, 2000)
}
async function doBuild() {
  try {
    await buildWeb()
    await loadRemote(false)
    pollRemote(() => rs.value?.build?.state !== 'running', () => {
      if (rs.value?.build?.state === 'done') flash('화면 빌드 완료 — 공개 주소에 반영됩니다')
      else if (rs.value?.build?.state === 'error') flash(`화면 빌드 실패: ${rs.value.build.error}`, 'err')
    })
  } catch (e) { flash(`빌드 요청 실패: ${errMsg(e)}`, 'err') }
}
async function doTunnel() {
  tunnelBusy.value = true
  try {
    const r = await startTunnel()
    await loadRemote(false)
    pollRemote(() => rs.value?.tunnel?.running || rs.value?.tunnel?.download?.state === 'error', () => {
      tunnelBusy.value = false
      if (rs.value?.tunnel?.running) setTimeout(() => loadRemote(true), 5000)
    })
    if (r.data?.state === 'service') flash('윈도우 서비스로 이미 실행 중입니다')
  } catch (e) {
    tunnelBusy.value = false
    flash(`연결 실패: ${errMsg(e)}`, 'err')
  }
}
async function doTunnelStop() {
  try { await stopTunnel(); await loadRemote(false); flash('커넥터를 껐습니다') } catch (e) { flash(errMsg(e), 'err') }
}

function fmtUptime(s) {
  if (s == null) return '-'
  if (s < 60) return `${s}초`
  if (s < 3600) return `${Math.floor(s / 60)}분`
  return `${Math.floor(s / 3600)}시간 ${Math.floor((s % 3600) / 60)}분`
}

watch(logAuto, (v) => {
  clearInterval(logTimer)
  if (v) logTimer = setInterval(loadLogs, 5000)
})

onMounted(async () => {
  await Promise.all([load(), loadStatus(), loadRemote(false)])
  loadLogs()
  loadOps()
  const cur = status.value?.sentiment?.installing
  if (cur) installing.value = true
  if (building.value) pollRemote(() => !building.value)
  else if (downloading.value) { tunnelBusy.value = true; pollRemote(() => !downloading.value, () => { tunnelBusy.value = false }) }
})
onBeforeUnmount(() => { clearInterval(logTimer); clearTimeout(toastTimer); clearInterval(remoteTimer) })
</script>

<style scoped>
.set {
  --bg: #0b0f14; --panel: #0f1419; --panel-2: #121821; --border: #2a3041; --subtle: #1e2533;
  --text: #e2e8f0; --text-2: #c9d4e0; --muted: #8892a4; --primary: #3b82f6;
  --red: #ef4444; --amber: #f59e0b; --green: #10b981;
  --mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  --sans: 'Noto Sans KR', 'Inter', -apple-system, 'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
  min-height: 100vh; background: var(--bg); color: var(--text); font-family: var(--sans);
  font-size: 13px; line-height: 1.5; color-scheme: dark; overflow-x: clip; padding-bottom: 80px;
}
.set *, .set *::before, .set *::after { box-sizing: border-box; }
button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
button:disabled { cursor: not-allowed; opacity: .55; }
h1, h2, p, dl, dd { margin: 0; }
code { font-family: var(--mono); font-size: 12px; background: var(--subtle); padding: 1px 5px; border-radius: 4px; }
.mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.small { font-size: 11.5px; }

.topbar {
  position: sticky; top: 0; z-index: 50; display: flex; align-items: center; justify-content: space-between;
  gap: 16px; min-height: 54px; padding: 8px 20px; background: rgba(11, 15, 20, .9);
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
.nav-btn { padding: 7px 12px; border-radius: 6px; font-size: 12.5px; font-weight: 500; color: var(--muted); white-space: nowrap; }
.nav-btn:hover { color: var(--text); background: var(--subtle); }
.nav-btn.active { color: var(--text); background: var(--subtle); box-shadow: inset 0 -2px 0 var(--primary); }
.pill { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; padding: 4px 10px; border-radius: 99px; border: 1px solid var(--border); color: var(--text-2); white-space: nowrap; }
.pill i { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); }
.pill.ok i { background: var(--green); box-shadow: 0 0 8px var(--green); }
.pill.warn i { background: var(--amber); }

.wrap { max-width: 1320px; margin: 0 auto; padding: 22px 20px; }
.intro { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 16px; }
.intro h1 { font-size: 22px; font-weight: 700; letter-spacing: -.02em; margin-bottom: 4px; }
.intro p { max-width: 760px; }
.banner { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 10px 14px; border-radius: 8px; margin-bottom: 12px; border: 1px solid; }
.banner.warn { background: rgba(245, 158, 11, .08); border-color: rgba(245, 158, 11, .35); color: #fcd34d; }
.banner.ok { background: rgba(16, 185, 129, .08); border-color: rgba(16, 185, 129, .35); color: #6ee7b7; }
.banner.err { background: rgba(239, 68, 68, .08); border-color: rgba(239, 68, 68, .35); color: #fca5a5; }

.grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 16px; align-items: start; }
.col-side { display: flex; flex-direction: column; gap: 16px; position: sticky; top: 70px; }
.col-main { display: flex; flex-direction: column; gap: 16px; }
.card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.card-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }
.card-head h2 { font-size: 13.5px; font-weight: 700; letter-spacing: -.01em; }
.setup-card {
  display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(360px, .8fr); gap: 28px; align-items: center;
  margin-bottom: 16px; padding: 24px; border: 1px solid rgba(59, 130, 246, .35); border-radius: 14px;
  background: linear-gradient(135deg, rgba(37, 99, 235, .14), rgba(16, 185, 129, .06));
}
.setup-card.ready { border-color: rgba(16, 185, 129, .35); }
.eyebrow { display: block; margin-bottom: 7px; color: #60a5fa; font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.setup-card.ready .eyebrow { color: #34d399; }
.setup-copy h2 { margin: 0 0 7px; font-size: 21px; }
.setup-copy p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.6; }
.setup-action { margin-top: 16px; text-decoration: none; }
.readiness { display: grid; gap: 7px; }
.ready-item {
  display: flex; align-items: center; gap: 11px; width: 100%; padding: 10px; border: 1px solid var(--subtle);
  border-radius: 9px; background: rgba(8, 11, 16, .42); color: var(--text); text-align: left;
}
button.ready-item:hover { border-color: var(--border); background: rgba(30, 41, 59, .65); }
.ready-item.static { cursor: default; }
.ready-item > span:last-child { display: flex; flex: 1; align-items: center; justify-content: space-between; gap: 8px; }
.ready-item b { font-size: 12.5px; }
.ready-item small { color: var(--muted); }
.ready-icon { display: grid; place-items: center; width: 25px; height: 25px; border-radius: 50%; background: rgba(245, 158, 11, .13); color: #fbbf24; font-size: 11px; font-weight: 700; }
.ready-icon.on { background: rgba(16, 185, 129, .14); color: #34d399; }
.section-panel { border: 1px solid var(--border); border-radius: 10px; background: var(--panel); overflow: hidden; }
.section-panel > summary {
  display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 15px 16px; cursor: pointer;
  list-style: none; user-select: none;
}
.section-panel > summary::-webkit-details-marker { display: none; }
.section-panel > summary::after { content: '⌄'; color: var(--muted); font-size: 18px; transition: transform .2s; }
.section-panel[open] > summary::after { transform: rotate(180deg); }
.section-panel > summary > span:first-child { display: flex; flex-direction: column; gap: 3px; }
.section-panel > summary b { font-size: 13.5px; }
.section-panel > summary small, .summary-state { color: var(--muted); font-size: 11.5px; }
.summary-state { margin-left: auto; }
.section-body { display: flex; flex-direction: column; gap: 12px; padding: 0 12px 12px; border-top: 1px solid var(--subtle); }
.section-body > .card { border-color: var(--subtle); }
.side-tools .section-body { padding-top: 12px; }
/* 원격 접속 */
.remote .pub { display: inline-block; margin-bottom: 10px; color: #60a5fa; text-decoration: none; font-size: 13px; }
.remote .pub:hover { text-decoration: underline; }
.rsteps { list-style: none; margin: 0 0 8px; padding: 0; display: flex; flex-direction: column; gap: 7px; }
.rsteps li { display: grid; grid-template-columns: 10px 64px 1fr auto; align-items: center; gap: 8px; font-size: 12px; min-width: 0; }
.rsteps li i { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
.rsteps li.ok i { background: var(--green); box-shadow: 0 0 6px rgba(16, 185, 129, .6); }
.rsteps li.todo i { background: var(--amber); }
.rsteps li.err i { background: var(--red); }
.rsteps li b { font-weight: 600; color: var(--text-2); }
.rsteps li span { color: var(--muted); min-width: 0; overflow-wrap: anywhere; }
.rsteps li.err span { color: #fca5a5; }
.rerr { color: #fca5a5; margin: 0 0 8px; overflow-wrap: anywhere; }
.how { margin-top: 4px; font-size: 12px; }
.how summary { cursor: pointer; color: var(--text-2); }
.how ol { margin: 8px 0; padding-left: 18px; display: flex; flex-direction: column; gap: 4px; color: var(--text-2); }
.how a { color: #60a5fa; }
@media (max-width: 480px) { .rsteps li { grid-template-columns: 10px 60px 1fr; } .rsteps li .btn { grid-column: 2 / -1; justify-self: start; } }
.card > p { margin-bottom: 8px; }

.row { display: grid; grid-template-columns: minmax(0, 300px) minmax(0, 1fr); gap: 16px; padding: 12px 0; border-top: 1px solid var(--subtle); }
.row:first-of-type { border-top: 0; }
.row.dirty { background: linear-gradient(90deg, rgba(59, 130, 246, .06), transparent 60%); margin: 0 -16px; padding-left: 16px; padding-right: 16px; }
.lbl { font-weight: 600; display: flex; align-items: center; gap: 6px; }
.tag { font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 4px; background: rgba(245, 158, 11, .12); color: #fbbf24; }
.key { font-size: 11px; color: var(--muted); margin-top: 1px; }
.help { font-size: 11.5px; color: var(--muted); margin-top: 4px; }
.row-ctl { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.cur { display: flex; gap: 6px; flex-wrap: wrap; }
.state { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; padding: 2px 8px; border-radius: 99px; background: var(--subtle); color: var(--text-2); max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.state i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); flex: none; }
.state.on i { background: var(--green); }
.state.off { color: var(--muted); }
.state.del { background: rgba(239, 68, 68, .12); color: #fca5a5; }
.state.pend { background: rgba(245, 158, 11, .12); color: #fbbf24; }
.inputs { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.pw { position: relative; flex: 1 1 240px; min-width: 0; }
.pw input, select {
  width: 100%; height: 32px; padding: 0 34px 0 10px; border-radius: 6px; border: 1px solid var(--border);
  background: var(--panel-2); color: var(--text); font-family: var(--mono); font-size: 12px;
}
select { flex: 1 1 240px; padding-right: 10px; font-family: var(--sans); }
.pw input:focus, select:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(59, 130, 246, .18); }
.eye { position: absolute; right: 6px; top: 50%; transform: translateY(-50%); color: var(--muted); padding: 4px; }
.eye:hover { color: var(--text); }
.test { font-size: 12px; padding: 6px 10px; border-radius: 6px; }
.test.ok { background: rgba(16, 185, 129, .08); color: #6ee7b7; }
.test.err { background: rgba(239, 68, 68, .08); color: #fca5a5; }

.btn { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 12px; border-radius: 6px; border: 1px solid var(--border); background: var(--panel-2); font-size: 12.5px; font-weight: 500; white-space: nowrap; }
.btn:hover:not(:disabled) { border-color: #3b4660; background: var(--subtle); }
.btn.sm { height: 28px; padding: 0 10px; font-size: 12px; }
.btn.primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.btn.primary:hover:not(:disabled) { background: #2563eb; }
.btn.ghost { background: transparent; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.kv { display: grid; grid-template-columns: 76px minmax(0, 1fr); gap: 6px 10px; font-size: 12px; }
.kv dt { color: var(--muted); }
.kv dd { min-width: 0; overflow-wrap: anywhere; }
.task-mini { font-size: 11.5px; }
.up-ok { color: #6ee7b7; }
.sent-state { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
.seg { display: inline-flex; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }
.seg button { font-size: 11.5px; padding: 3px 9px; color: var(--muted); }
.seg button.on { background: var(--subtle); color: var(--text); }
.logs { height: 280px; overflow: auto; background: #080b10; border: 1px solid var(--subtle); border-radius: 6px; padding: 8px; font-size: 10.5px; line-height: 1.55; white-space: pre-wrap; word-break: break-all; color: #a3b1c6; }
.l-err { color: #fca5a5; }
.l-warn { color: #fcd34d; }
.auto { display: flex; align-items: center; gap: 6px; margin-top: 8px; font-size: 11.5px; color: var(--muted); cursor: pointer; }

.savebar {
  position: fixed; left: 50%; bottom: 18px; transform: translateX(-50%); z-index: 60; display: flex; align-items: center;
  gap: 18px; padding: 10px 12px 10px 18px; border-radius: 10px; background: #111827; border: 1px solid #334155;
  box-shadow: 0 12px 40px rgba(0, 0, 0, .5); max-width: calc(100vw - 32px);
}
.savebar > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sb-actions { display: flex; gap: 8px; flex: none; }
.toast { position: fixed; right: 18px; top: 66px; z-index: 70; padding: 10px 14px; border-radius: 8px; font-size: 12.5px; background: #0f2a20; border: 1px solid rgba(16, 185, 129, .4); color: #a7f3d0; max-width: min(420px, calc(100vw - 36px)); }
.toast.err { background: #2a1111; border-color: rgba(239, 68, 68, .4); color: #fecaca; }
.bar-enter-active, .bar-leave-active { transition: all .2s ease; }
.bar-enter-from, .bar-leave-to { opacity: 0; transform: translate(-50%, 10px); }
.toast.bar-enter-from, .toast.bar-leave-to { transform: translateY(-6px); }

@media (max-width: 1080px) {
  .setup-card { grid-template-columns: minmax(0, 1fr); }
  .grid { grid-template-columns: minmax(0, 1fr); }
  .col-side { position: static; }
}
@media (max-width: 720px) {
  .topbar { flex-wrap: wrap; padding: 8px 16px; }
  .tb-left { gap: 12px; flex-wrap: wrap; }
  .wrap { padding: 16px; }
  .intro { flex-direction: column; }
  .setup-card { padding: 18px; gap: 18px; }
  .ready-item > span:last-child { align-items: flex-start; flex-direction: column; gap: 2px; }
  .row { grid-template-columns: minmax(0, 1fr); gap: 8px; }
  .savebar { left: 16px; right: 16px; transform: none; gap: 10px; }
  .bar-enter-from, .bar-leave-to { transform: translateY(10px); }
}
.pkg-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; padding: 8px 0; border-top: 1px solid var(--subtle); }
.pkg-row b { font-size: 12.5px; }
.pkg-row .state { margin: 4px 6px 0 0; }
.ops h2 .small { font-weight: 400; margin-left: 6px; }
.ops-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin-bottom: 8px; }
.okpi { background: var(--panel-2, #121821); border: 1px solid var(--subtle); border-radius: 8px; padding: 9px 11px; }
.okpi span { display: block; font-size: 11px; color: var(--muted); }
.okpi b { font-size: 17px; font-variant-numeric: tabular-nums; }
.okpi small { display: block; font-size: 11px; color: var(--muted); }
.ops-bars { display: flex; align-items: flex-end; gap: 3px; height: 78px; margin: 10px 0 4px; padding-bottom: 14px; position: relative; }
.bar-col { flex: 1; height: 100%; display: flex; flex-direction: column; justify-content: flex-end; align-items: center; position: relative; min-width: 0; }
.bar-col i { display: block; width: 100%; max-width: 18px; background: #3b82f6; border-radius: 3px 3px 0 0; }
.bar-col:hover i { background: #60a5fa; }
.bar-col span { position: absolute; bottom: -14px; font-size: 9.5px; color: var(--muted); font-variant-numeric: tabular-nums; }
.ops-tables { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 8px; }
.ops-tables h3 { font-size: 12px; font-weight: 600; margin: 8px 0 4px; color: var(--text-2, #c9d4e0); }
.otable { width: 100%; border-collapse: collapse; font-size: 12px; }
.otable th { text-align: left; color: var(--muted); font-weight: 500; padding: 4px 6px; border-bottom: 1px solid var(--border); }
.otable td { padding: 4px 6px; border-bottom: 1px solid var(--subtle); }
.otable .r { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.otable .bad { color: #fca5a5; }
.ops-err { margin-top: 8px; }
.ops .card-head { gap: 8px; }
.selftest { border: 1px solid var(--subtle); border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; background: var(--panel-2, #121821); }
.st-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.selftest .live { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 2px 14px; font-size: 12px; }
.selftest .live li.ok b { color: #6ee7b7; font-weight: 600; }
.selftest .live li.bad b { color: #fca5a5; font-weight: 600; }
.bad-t { color: #fca5a5; margin-top: 6px; }
.st-tail { white-space: pre-wrap; font-size: 11px; max-height: 260px; overflow: auto; background: #0b0f14; padding: 8px; border-radius: 6px; }
.ops-err div { padding: 3px 0; border-bottom: 1px dashed var(--subtle); }
@media (max-width: 900px) { .ops-tables { grid-template-columns: 1fr; } }
</style>
