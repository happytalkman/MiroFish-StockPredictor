<template>
  <div class="kg-step">
    <!-- 상단 도구 모음 -->
    <div class="kg-toolbar">
      <div class="kg-layers">
        <button
          v-for="l in LAYERS" :key="l.key"
          class="layer-chip" :class="{ off: !layerOn[l.key] }"
          :style="{ '--c': l.color }"
          @click="layerOn[l.key] = !layerOn[l.key]"
        >
          <span class="dot"></span>{{ l.label }}
          <b>{{ counts[l.key] || 0 }}</b>
        </button>
      </div>
      <div class="kg-actions">
        <button class="btn ghost" :disabled="busy" @click="load(true)">↻ 그래프 재생성</button>
        <button class="btn ghost" :disabled="busy || !kg" @click="onRegenInsight">✦ 인사이트 재작성</button>
        <a class="btn ghost" :class="{ disabled: !kg }" :href="kg ? kgTtlUrl(reportId) : undefined" download>⬇ RDF/OWL2 (.ttl)</a>
        <router-link v-if="props.stock?.ticker" class="btn ghost" :to="{ path: '/ontology', query: { ticker: props.stock.ticker } }" title="여러 보고서를 쌓은 전역 그래프에서 이 종목 보기">누적 온톨로지</router-link>
        <button class="btn primary" :disabled="busy || !dirty" @click="onSave">💾 편집 저장{{ dirty ? ' *' : '' }}</button>
      </div>
    </div>

    <!-- 로딩 / 오류 -->
    <div v-if="busy && !kg" class="kg-loading">
      <div class="spinner"></div>
      <div>
        <div class="kg-loading-title">{{ busyMsg || '지식그래프 준비 중…' }}</div>
        <div class="kg-loading-sub">기본 S-P-O 그래프를 먼저 그리고, AI 가 트리플·핵심 질문(CQ)·의사결정 레코드(ADR)·인사이트를 병렬로 추가합니다.</div>
      </div>
    </div>

    <ProgressModal
      :show="pm.show"
      :title="pmTitle"
      subtitle="기본 S-P-O 그래프는 바로 표시되고, AI 확장 결과는 끝나는 대로 합쳐집니다."
      done-title="지식그래프 확장 완료"
      :stages="pm.stages" :progress="pm.progress" :message="pm.message" :logs="pm.logs"
      :status="pm.status" :error="pm.error" :started-at="pm.startedAt" :expected-sec="60"
      @close="pmClose" @retry="pmClose(); load(true)"
    />
    <div v-if="error" class="kg-error">{{ error }} <button class="btn ghost sm" @click="load(false)">재시도</button></div>

    <template v-if="kg">
      <div class="kg-stats">
        노드 <b>{{ kg.nodes.length }}</b> · 엣지 <b>{{ kg.edges.length }}</b> · CQ <b>{{ kg.cqs.length }}</b> · ADR <b>{{ kg.adrs.length }}</b>
        <span v-if="kg.model" class="muted"> · {{ kg.model }}</span>
        <span v-if="kg.edited" class="tag">편집됨</span>
        <span v-if="busy" class="muted"> · {{ busyMsg }}</span>
        <span v-if="kg.enriching" class="tag live">AI 확장 중…</span>
        <div class="view-toggle">
          <button :class="{ on: view === '2d' }" @click="setView('2d')">2D · d3.js</button>
          <button :class="{ on: view === '3d' }" @click="setView('3d')">3D · three.js</button>
          <button :class="{ on: view === 'table' }" @click="setView('table')">S-P-O 트리플 표</button>
        </div>
      </div>

      <!-- 그래프 + 편집 패널 -->
      <div class="kg-main">
        <div class="kg-canvas" ref="canvasEl">
          <svg ref="svgEl" v-show="view === '2d'"></svg>
          <Graph3D
            v-if="view === '3d'"
            :nodes="kg.nodes" :edges="kg.edges" :layer-on="layerOn"
            :selected-id="selNode ? selNode.id : null" :version="graphVersion"
            @select="onSelect3d"
          />
          <div v-if="view === 'table'" class="spo-table">
            <div class="spo-head">
              <input v-model="spoQuery" placeholder="주어·술어·목적어 검색" />
              <span class="muted">{{ spoRows.length }} / {{ kg.edges.length }} 트리플</span>
            </div>
            <div class="spo-scroll">
              <table>
                <thead><tr><th>Subject 주어</th><th>Predicate 술어</th><th>Object 목적어</th><th>계층</th></tr></thead>
                <tbody>
                  <tr v-for="r in spoRows" :key="r.e.id" :class="{ sel: selEdge === r.e }" @click="selectEdge(r.e)">
                    <td><i :style="{ background: layerColor(r.sl) }"></i>{{ r.s }}</td>
                    <td class="pred">{{ r.e.predicate }}</td>
                    <td><i :style="{ background: layerColor(r.ol) }"></i>{{ r.o }}</td>
                    <td :style="{ color: layerColor(r.e.layer) }">{{ r.e.layer }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="kg-hint" v-show="view === '2d'">
            드래그: 노드 이동 · 휠: 확대/축소 · 클릭: 선택 ·
            <b :class="{ on: linkMode }">{{ linkMode ? '연결 모드: 출발 → 도착 노드를 차례로 클릭' : 'Shift+클릭 두 번: 엣지 연결' }}</b>
          </div>
        </div>

        <div class="kg-panel">
          <!-- 노드 편집 -->
          <div v-if="selNode" class="panel-box">
            <div class="panel-title">노드 편집 <span class="muted">#{{ selNode.id }}</span></div>
            <label>이름<input v-model="nodeForm.label" /></label>
            <div class="row2">
              <label>계층
                <select v-model="nodeForm.layer" @change="nodeForm.type = TYPES[nodeForm.layer][0]">
                  <option v-for="l in LAYERS" :key="l.key" :value="l.key">{{ l.label }}</option>
                </select>
              </label>
              <label>유형
                <select v-model="nodeForm.type">
                  <option v-for="t in typeOptions(nodeForm.layer, nodeForm.type)" :key="t" :value="t">{{ t }}</option>
                </select>
              </label>
            </div>
            <label>값<input v-model="nodeForm.value" placeholder="수치·상태" /></label>
            <label>설명<textarea v-model="nodeForm.description" rows="3"></textarea></label>
            <a v-if="/^https?:\/\//i.test(selNode.url || '')" :href="selNode.url" target="_blank" rel="noopener noreferrer" class="node-src">출처 기사 열기 ↗</a>
            <div class="panel-btns">
              <button class="btn primary sm" @click="applyNode">적용</button>
              <button class="btn ghost sm" @click="startLinkFrom(selNode)">↦ 여기서 연결</button>
              <button class="btn danger sm" @click="deleteNode(selNode)">삭제</button>
            </div>
            <div class="panel-sub">연결된 관계</div>
            <ul class="rel-list">
              <li v-for="e in relatedEdges(selNode.id)" :key="e.id" @click="selectEdge(e)">
                {{ nodeLabel(idOf(e.source)) }} <i>{{ e.predicate }}</i> {{ nodeLabel(idOf(e.target)) }}
              </li>
              <li v-if="!relatedEdges(selNode.id).length" class="muted">없음</li>
            </ul>
          </div>

          <!-- 엣지 편집 -->
          <div v-else-if="selEdge" class="panel-box">
            <div class="panel-title">관계 편집</div>
            <div class="edge-desc">{{ nodeLabel(idOf(selEdge.source)) }} → {{ nodeLabel(idOf(selEdge.target)) }}</div>
            <label>관계명 (predicate)<input v-model="edgeForm.predicate" /></label>
            <div class="row2">
              <label>출발
                <select v-model="edgeForm.source"><option v-for="n in kg.nodes" :key="n.id" :value="n.id">{{ n.label }}</option></select>
              </label>
              <label>도착
                <select v-model="edgeForm.target"><option v-for="n in kg.nodes" :key="n.id" :value="n.id">{{ n.label }}</option></select>
              </label>
            </div>
            <label>계층
              <select v-model="edgeForm.layer"><option v-for="l in LAYERS" :key="l.key" :value="l.key">{{ l.label }}</option></select>
            </label>
            <div class="panel-btns">
              <button class="btn primary sm" @click="applyEdge">적용</button>
              <button class="btn danger sm" @click="deleteEdge(selEdge)">삭제</button>
            </div>
          </div>

          <!-- 추가 -->
          <div v-else class="panel-box">
            <div class="panel-title">노드 추가</div>
            <label>이름<input v-model="newNode.label" placeholder="예: 외국인 순매수" /></label>
            <div class="row2">
              <label>계층
                <select v-model="newNode.layer" @change="newNode.type = TYPES[newNode.layer][0]">
                  <option v-for="l in LAYERS" :key="l.key" :value="l.key">{{ l.label }}</option>
                </select>
              </label>
              <label>유형
                <select v-model="newNode.type"><option v-for="t in TYPES[newNode.layer]" :key="t" :value="t">{{ t }}</option></select>
              </label>
            </div>
            <label>값<input v-model="newNode.value" /></label>
            <button class="btn primary sm full" :disabled="!newNode.label.trim()" @click="addNode">+ 노드 추가</button>

            <div class="panel-title mt">관계(엣지) 추가</div>
            <div class="row2">
              <label>출발
                <select v-model="newEdge.source"><option value="">선택</option><option v-for="n in sortedNodes" :key="n.id" :value="n.id">{{ n.label }}</option></select>
              </label>
              <label>도착
                <select v-model="newEdge.target"><option value="">선택</option><option v-for="n in sortedNodes" :key="n.id" :value="n.id">{{ n.label }}</option></select>
              </label>
            </div>
            <label>관계명<input v-model="newEdge.predicate" placeholder="예: triggers, mitigates" /></label>
            <button class="btn primary sm full" :disabled="!newEdge.source || !newEdge.target || newEdge.source === newEdge.target" @click="addEdge()">+ 관계 추가</button>
            <p class="muted small">노드를 클릭하면 편집, Shift+클릭 두 번이면 바로 연결됩니다.</p>
          </div>
        </div>
      </div>

      <!-- 음성 종합 의견 -->
      <div class="voice-bar">
        <div class="voice-left">
          <span class="voice-icon">🔊</span>
          <div>
            <div class="voice-title">종합 의견 음성 브리핑</div>
            <div class="voice-sub">{{ voiceStatus }}</div>
          </div>
        </div>
        <div class="voice-ctrl">
          <select v-model="voice" :disabled="voiceBusy">
            <option v-for="v in VOICES" :key="v.id" :value="v.id">{{ v.label }}</option>
          </select>
          <button class="btn primary sm" :disabled="voiceBusy || !kg.spoken_summary" @click="play">
            {{ voiceBusy ? '생성 중…' : (playing ? '❚❚ 일시정지' : '▶ 재생') }}
          </button>
          <button class="btn ghost sm" :disabled="!playing && !paused" @click="stop">■ 정지</button>
          <button class="btn ghost sm" :disabled="voiceBusy || !kg.spoken_summary" @click="download">⬇ 다운로드</button>
        </div>
      </div>
      <details class="spoken">
        <summary>음성 원고 보기 · 수정</summary>
        <textarea v-model="kg.spoken_summary" rows="5" @input="onSpokenEdit"></textarea>
      </details>

      <!-- 탭 -->
      <div class="kg-tabs">
        <button v-for="t in TABS" :key="t.key" :class="{ active: tab === t.key }" @click="tab = t.key">
          {{ t.label }}<span v-if="t.count" class="cnt">{{ t.count() }}</span>
        </button>
      </div>

      <div v-if="tab === 'insight'" class="tab-body md" v-html="insightHtml"></div>

      <div v-else-if="tab === 'cq'" class="tab-body">
        <div v-for="c in kg.cqs" :key="c.id" class="cq-item" :style="{ '--c': layerColor(c.layer) }">
          <div class="cq-head"><span class="badge">{{ c.id }}</span><span class="layer-tag">{{ layerName(c.layer) }}</span></div>
          <textarea v-model="c.question" class="cq-q" rows="1" @input="dirty = true"></textarea>
          <textarea v-model="c.answer" class="cq-a" rows="2" @input="dirty = true"></textarea>
          <div class="chips">
            <span v-for="nid in c.node_ids" :key="nid" class="chip" @click="focusNode(nid)">{{ nodeLabel(nid) }}</span>
          </div>
        </div>
      </div>

      <div v-else-if="tab === 'adr'" class="tab-body adr-grid">
        <div v-for="a in kg.adrs" :key="a.id" class="adr-card">
          <div class="adr-head">
            <span class="badge">{{ a.id }}</span>
            <select v-model="a.status" class="adr-status" :class="'st-' + a.status" @change="dirty = true">
              <option v-for="s in ['제안', '채택', '보류', '기각']" :key="s" :value="s">{{ s }}</option>
            </select>
            <span class="adr-conf">확신도 <input type="number" min="0" max="100" v-model.number="a.confidence" @input="dirty = true" />%</span>
          </div>
          <input v-model="a.title" class="adr-title" @input="dirty = true" />
          <dl>
            <dt>맥락</dt><dd><textarea v-model="a.context" rows="2" @input="dirty = true"></textarea></dd>
            <dt>결정</dt><dd><textarea v-model="a.decision" rows="2" @input="dirty = true"></textarea></dd>
            <dt>근거</dt><dd><textarea v-model="a.rationale" rows="2" @input="dirty = true"></textarea></dd>
            <dt>결과</dt><dd><textarea v-model="a.consequences" rows="2" @input="dirty = true"></textarea></dd>
          </dl>
          <div class="chips">
            <span v-for="nid in a.evidence" :key="nid" class="chip" @click="focusNode(nid)">{{ nodeLabel(nid) }}</span>
          </div>
        </div>
      </div>

      <div v-else-if="tab === 'ttl'" class="tab-body">
        <div class="ttl-head">
          <span class="muted">저장할 때마다 편집된 그래프로 다시 생성됩니다.</span>
          <button class="btn ghost sm" @click="copyTtl">{{ copied ? '복사됨' : '복사' }}</button>
        </div>
        <pre class="ttl">{{ kg.ttl }}</pre>
      </div>

      <div class="step-nav">
        <button class="btn ghost" @click="$emit('back')">← 3단계 보고서</button>
        <button class="btn primary lg" :disabled="busy" @click="goNext">5단계 판단으로 →</button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import * as d3 from 'd3'
import {
  getKnowledgeGraph, generateKnowledgeGraph, saveKnowledgeGraph, regenerateInsight, kgTtlUrl, synthesizeSpeech
} from '../api/stock.js'
import ProgressModal from './ProgressModal.vue'
import Graph3D from './Graph3D.vue'
import { useProgress } from '../composables/useProgress.js'

const props = defineProps({ reportId: { type: String, required: true }, stock: { type: Object, default: null } })
const emit = defineEmits(['next', 'back'])

const LAYERS = [
  { key: 'semantic', label: 'Semantic 의미', color: '#3b82f6' },
  { key: 'kinetic', label: 'Kinetic 행위', color: '#f59e0b' },
  { key: 'dynamic', label: 'Dynamic 변화·결정', color: '#10b981' }
]
const TYPES = {
  semantic: ['ObjectType', 'Property', 'LinkType', 'Metric', 'Entity'],
  kinetic: ['ActionType', 'Function', 'Rule', 'Signal'],
  dynamic: ['Event', 'Scenario', 'Decision', 'Risk', 'State']
}
const VOICES = [
  { id: 'eve', label: 'Eve (여성·차분)' }, { id: 'luna', label: 'Luna (여성·밝음)' },
  { id: 'orion', label: 'Orion (남성·안정)' }, { id: 'atlas', label: 'Atlas (남성·깊음)' },
  { id: 'helix', label: 'Helix' }, { id: 'perseus', label: 'Perseus' }
]

const kg = ref(null)
const busy = ref(false)
const busyMsg = ref('')
const error = ref('')
const dirty = ref(false)
const tab = ref('insight')
const layerOn = reactive({ semantic: true, kinetic: true, dynamic: true })
const selNode = ref(null)
const selEdge = ref(null)
const nodeForm = reactive({ label: '', layer: 'semantic', type: 'ObjectType', value: '', description: '' })
const edgeForm = reactive({ predicate: '', source: '', target: '', layer: 'semantic' })
const newNode = reactive({ label: '', layer: 'semantic', type: 'ObjectType', value: '' })
const newEdge = reactive({ source: '', target: '', predicate: 'relatedTo' })
const linkMode = ref(false)
let linkFrom = null
const copied = ref(false)

const TABS = [
  { key: 'insight', label: '인사이트 보고서' },
  { key: 'cq', label: '핵심 질문 CQ', count: () => kg.value?.cqs.length },
  { key: 'adr', label: '의사결정 레코드 ADR', count: () => kg.value?.adrs.length },
  { key: 'ttl', label: 'RDF / OWL 2' }
]

const counts = computed(() => {
  const c = {}
  for (const n of kg.value?.nodes || []) c[n.layer] = (c[n.layer] || 0) + 1
  return c
})
const sortedNodes = computed(() => [...(kg.value?.nodes || [])].sort((a, b) => a.label.localeCompare(b.label, 'ko')))
const layerColor = (l) => LAYERS.find(x => x.key === l)?.color || '#94a3b8'
const layerName = (l) => LAYERS.find(x => x.key === l)?.label || l
const nodeLabel = (id) => kg.value?.nodes.find(n => n.id === id)?.label || id
const relatedEdges = (id) => (kg.value?.edges || []).filter(e => idOf(e.source) === id || idOf(e.target) === id)
const typeOptions = (layer, cur) => (TYPES[layer].includes(cur) ? TYPES[layer] : [cur, ...TYPES[layer]])

// ─────────────────────────────────────────── 로드/생성
const { pm, start: pmStart, log: pmLog, stage: pmStage, done: pmDone, fail: pmFail, close: pmClose, follow } = useProgress()
const pmTitle = ref('지식그래프 생성 중')
const view = ref('2d')
const graphVersion = ref(0)
const spoQuery = ref('')
const KG_STAGES = [
  { key: 'baseline', label: '기본 S-P-O 그래프' },
  { key: 'triples', label: 'S-P-O 트리플 확장' },
  { key: 'cqs', label: '핵심 질문(CQ)' },
  { key: 'adrs', label: '의사결정 레코드(ADR)' },
  { key: 'insight', label: '인사이트·음성 원고' },
  { key: 'owl', label: 'RDF/OWL 2 변환' }
]

function setView(v) {
  view.value = v
  if (v === '2d') nextTick(renderGraph)
}
function onSelect3d(id) {
  const n = id && kg.value?.nodes.find(x => x.id === id)
  if (n) selectNode(n)
  else clearSel()
}
const spoRows = computed(() => {
  const q = spoQuery.value.trim().toLowerCase()
  const byId = new Map((kg.value?.nodes || []).map(n => [n.id, n]))
  return (kg.value?.edges || []).map(e => {
    const s = byId.get(idOf(e.source)), o = byId.get(idOf(e.target))
    return { e, s: s?.label || idOf(e.source), o: o?.label || idOf(e.target), sl: s?.layer, ol: o?.layer }
  }).filter(r => !q || `${r.s} ${r.e.predicate} ${r.o}`.toLowerCase().includes(q))
})

async function refreshKg() {
  try {
    const r = await getKnowledgeGraph(props.reportId)
    const keepPos = new Map(simNodes.map(n => [n.id, { x: n.x, y: n.y }]))
    for (const n of r.data.nodes) { const p = keepPos.get(n.id); if (p) { n.x = p.x; n.y = p.y } }
    setKg(r.data)
    return r.data
  } catch (e) { return null }
}

async function runTask(taskId, title) {
  pmTitle.value = title
  pmStart(KG_STAGES.map(s => ({ ...s, status: 'pending' })))
  pmLog(`${props.stock?.name || ''} 지식그래프 작업 시작`)
  let baselineShown = !!kg.value
  try {
    await follow(taskId, {
      onUpdate: async (t) => {
        const d = t.progress_detail || {}
        if (d.baseline || t.progress >= 10) {
          pmStage('baseline', 'done')
          if (!baselineShown) { baselineShown = true; await refreshKg() }
        } else pmStage('baseline', 'running')
        for (const [k, j] of Object.entries(d.jobs || {})) {
          pmStage(k, j.status === 'done' ? 'done' : j.status === 'error' ? 'error' : 'running',
            j.status === 'running' ? `${(j.chars || 0).toLocaleString()}자 · ${j.sec || 0}초` : j.status === 'error' ? '실패' : '')
        }
        if (t.progress >= 92) pmStage('owl', t.progress >= 100 ? 'done' : 'running')
      }
    })
    pmStage('owl', 'done')
    const data = await refreshKg()
    const errs = Object.entries(data?.job_errors || {}).filter(([, v]) => v)
    if (errs.length) pmLog(`일부 작업 실패: ${errs.map(([k]) => k).join(', ')} — 나머지 결과로 완성했습니다`, 'err')
    pmDone(`완료 — 노드 ${data?.nodes.length} · 엣지 ${data?.edges.length} · CQ ${data?.cqs.length} · ADR ${data?.adrs.length}`)
  } catch (e) {
    pmFail(e.message)
    await refreshKg()
  }
}

async function load(force = false) {
  error.value = ''
  if (force && dirty.value && !confirmDiscard()) return
  busy.value = true
  busyMsg.value = ''
  try {
    if (!force) {
      try {
        const r = await getKnowledgeGraph(props.reportId)
        setKg(r.data)
        if (!r.data.enriching) return
      } catch (e) { /* 없으면 생성 */ }
    }
    const r = await generateKnowledgeGraph(props.reportId, force)
    if (r.data?.task_id) {
      busy.value = false
      await runTask(r.data.task_id, force ? '지식그래프 재생성 중' : '지식그래프 생성 중')
    } else if (r.data?.nodes) {
      setKg(r.data)
    }
  } catch (e) {
    error.value = e.response?.data?.error || e.message || '지식그래프 생성 실패'
  } finally {
    busy.value = false
    busyMsg.value = ''
  }
}
function confirmDiscard() { return window.confirm('저장하지 않은 편집 내용이 사라집니다. 계속할까요?') }

function setKg(data) {
  kg.value = data
  dirty.value = false
  selNode.value = null
  selEdge.value = null
  resetAudio()
  nextTick(renderGraph)
}

function payload() {
  const k = kg.value
  return {
    nodes: k.nodes.map(({ id, label, layer, type, description, value }) => ({ id, label, layer, type, description, value })),
    edges: k.edges.map(({ id, source, target, predicate, layer }) => ({
      id, predicate, layer,
      source: typeof source === 'object' ? source.id : source,
      target: typeof target === 'object' ? target.id : target
    })),
    cqs: k.cqs, adrs: k.adrs,
    insight_report: k.insight_report, spoken_summary: k.spoken_summary
  }
}

async function onSave() {
  busy.value = true
  busyMsg.value = '저장하고 RDF/OWL2 를 다시 만드는 중…'
  try {
    const r = await saveKnowledgeGraph(props.reportId, payload())
    const keepPos = new Map(simNodes.map(n => [n.id, { x: n.x, y: n.y }]))
    setKg(r.data)
    nextTick(() => { for (const n of simNodes) { const p = keepPos.get(n.id); if (p) { n.x = p.x; n.y = p.y } } })
  } catch (e) {
    error.value = e.response?.data?.error || e.message
  } finally { busy.value = false; busyMsg.value = '' }
}

async function onRegenInsight() {
  if (dirty.value) await onSave()
  try {
    const r = await regenerateInsight(props.reportId)
    pmTitle.value = '인사이트 재작성 중'
    pmStart([{ key: 'read', label: '편집된 그래프 읽기', status: 'done' }, { key: 'write', label: '인사이트·음성 원고 작성', status: 'running' }, { key: 'owl', label: 'RDF/OWL 2 갱신', status: 'pending' }])
    await follow(r.data.task_id)
    pmStage('write', 'done'); pmStage('owl', 'done')
    const g = await getKnowledgeGraph(props.reportId)
    kg.value.insight_report = g.data.insight_report
    kg.value.spoken_summary = g.data.spoken_summary
    kg.value.ttl = g.data.ttl
    resetAudio()
    tab.value = 'insight'
    pmDone('인사이트 재작성 완료')
  } catch (e) {
    if (pm.show) pmFail(e.message)
    else error.value = e.response?.data?.error || e.message
  }
}

async function goNext() {
  if (dirty.value) await onSave()
  emit('next')
}

// ─────────────────────────────────────────── 편집
function selectNode(n) {
  selEdge.value = null
  selNode.value = n
  Object.assign(nodeForm, { label: n.label, layer: n.layer, type: n.type, value: n.value || '', description: n.description || '' })
  highlight()
}
function selectEdge(e) {
  selNode.value = null
  selEdge.value = e
  Object.assign(edgeForm, { predicate: e.predicate, layer: e.layer, source: idOf(e.source), target: idOf(e.target) })
  highlight()
}
const idOf = (x) => (typeof x === 'object' ? x.id : x)
function clearSel() { selNode.value = null; selEdge.value = null; linkMode.value = false; linkFrom = null; highlight() }

function applyNode() {
  Object.assign(selNode.value, { ...nodeForm })
  dirty.value = true
  renderGraph()
}
function deleteNode(n) {
  if (!window.confirm(`'${n.label}' 노드와 연결된 관계 ${relatedEdges(n.id).length}개를 삭제할까요?`)) return
  kg.value.nodes = kg.value.nodes.filter(x => x.id !== n.id)
  kg.value.edges = kg.value.edges.filter(e => idOf(e.source) !== n.id && idOf(e.target) !== n.id)
  for (const c of kg.value.cqs) c.node_ids = c.node_ids.filter(x => x !== n.id)
  for (const a of kg.value.adrs) a.evidence = a.evidence.filter(x => x !== n.id)
  dirty.value = true
  clearSel()
  renderGraph()
}
function applyEdge() {
  Object.assign(selEdge.value, { predicate: edgeForm.predicate || 'relatedTo', layer: edgeForm.layer, source: edgeForm.source, target: edgeForm.target })
  dirty.value = true
  renderGraph()
}
function deleteEdge(e) {
  kg.value.edges = kg.value.edges.filter(x => x !== e)
  dirty.value = true
  clearSel()
  renderGraph()
}
function uniqueId(base) {
  const ids = new Set(kg.value.nodes.map(n => n.id))
  let id = base.replace(/[^0-9A-Za-z_]/g, '_').replace(/^_+|_+$/g, '') || 'node'
  if (!/^[A-Za-z]/.test(id)) id = 'n_' + id
  let k = 2, out = id
  while (ids.has(out)) out = `${id}_${k++}`
  return out
}
function addNode() {
  const n = { id: uniqueId('user_' + Date.now().toString(36)), label: newNode.label.trim(), layer: newNode.layer, type: newNode.type, value: newNode.value, description: '' }
  const c = simNodes.length ? d3.polygonCentroid(simNodes.map(p => [p.x, p.y])) : [0, 0]
  n.x = (c[0] || 0) + 30; n.y = (c[1] || 0) + 30
  kg.value.nodes.push(n)
  newNode.label = ''; newNode.value = ''
  dirty.value = true
  renderGraph()
  selectNode(n)
}
function addEdge(src, tgt, pred) {
  const s = src || newEdge.source, t = tgt || newEdge.target
  if (!s || !t || s === t) return
  const layer = kg.value.nodes.find(n => n.id === s)?.layer || 'semantic'
  const e = { id: 'e_' + Date.now().toString(36), source: s, target: t, predicate: pred || newEdge.predicate || 'relatedTo', layer }
  kg.value.edges.push(e)
  newEdge.source = ''; newEdge.target = ''; newEdge.predicate = 'relatedTo'
  dirty.value = true
  renderGraph()
  selectEdge(e)
}
function startLinkFrom(n) { linkMode.value = true; linkFrom = n.id }
function focusNode(id) {
  const n = kg.value.nodes.find(x => x.id === id)
  if (!n) return
  selectNode(n)
  if (svgSel && zoomer && n.x != null) {
    const { width, height } = size()
    svgSel.transition().duration(500).call(zoomer.transform, d3.zoomIdentity.translate(width / 2, height / 2).scale(1.3).translate(-n.x, -n.y))
  }
}
function onSpokenEdit() { dirty.value = true; resetAudio() }

// ─────────────────────────────────────────── d3 그래프
const svgEl = ref(null)
const canvasEl = ref(null)
let svgSel = null, gRoot = null, zoomer = null, sim = null
let simNodes = []
let linkSel = null, nodeSel = null, labelSel = null

function size() {
  const r = canvasEl.value?.getBoundingClientRect()
  return { width: r?.width || 800, height: r?.height || 560 }
}

function renderGraph() {
  graphVersion.value++
  if (!kg.value || !svgEl.value) return
  const { width, height } = size()
  if (!svgSel) {
    svgSel = d3.select(svgEl.value)
    const defs = svgSel.append('defs')
    for (const l of LAYERS) {
      defs.append('marker').attr('id', `arr-${l.key}`).attr('viewBox', '0 -5 10 10').attr('refX', 22).attr('refY', 0)
        .attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto')
        .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', l.color)
    }
    gRoot = svgSel.append('g')
    gRoot.append('g').attr('class', 'links')
    gRoot.append('g').attr('class', 'elabels')
    gRoot.append('g').attr('class', 'nodes')
    zoomer = d3.zoom().scaleExtent([0.2, 4]).on('zoom', (ev) => gRoot.attr('transform', ev.transform))
    svgSel.call(zoomer).on('dblclick.zoom', null)
    svgSel.on('click', (ev) => { if (ev.target === svgEl.value) clearSel() })
  }
  svgSel.attr('viewBox', [0, 0, width, height])

  const old = new Map(simNodes.map(n => [n.id, n]))
  const visible = new Set(kg.value.nodes.filter(n => layerOn[n.layer]).map(n => n.id))
  simNodes = kg.value.nodes.filter(n => visible.has(n.id)).map(n => {
    const o = old.get(n.id)
    if (o) { n.x = n.x ?? o.x; n.y = n.y ?? o.y; n.vx = o.vx; n.vy = o.vy; n.fx = o.fx; n.fy = o.fy }
    return n
  })
  const links = kg.value.edges
    .filter(e => visible.has(idOf(e.source)) && visible.has(idOf(e.target)))
    .map(e => { e.source = idOf(e.source); e.target = idOf(e.target); return e })
  const deg = {}
  for (const e of links) { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1 }
  const radius = (n) => 7 + Math.min(10, (deg[n.id] || 0) * 1.3)

  // 계층별 가로 띠로 배치 유도
  const bandX = { semantic: width * 0.22, kinetic: width * 0.5, dynamic: width * 0.78 }

  linkSel = gRoot.select('.links').selectAll('line').data(links, d => d.id)
    .join('line')
    .attr('stroke', d => layerColor(d.layer)).attr('stroke-opacity', 0.55).attr('stroke-width', 1.4)
    .attr('marker-end', d => `url(#arr-${d.layer})`)
    .style('cursor', 'pointer')
    .on('click', (ev, d) => { ev.stopPropagation(); selectEdge(d) })

  const elabels = gRoot.select('.elabels').selectAll('text').data(links, d => d.id)
    .join('text').attr('class', 'elabel').text(d => d.predicate)

  nodeSel = gRoot.select('.nodes').selectAll('g.node').data(simNodes, d => d.id)
    .join(enter => {
      const g = enter.append('g').attr('class', 'node').style('cursor', 'pointer')
      g.append('circle')
      g.append('text').attr('class', 'nlabel').attr('dy', '0.32em')
      return g
    })
  nodeSel.select('circle')
    .attr('r', radius)
    .attr('fill', d => layerColor(d.layer))
    .attr('fill-opacity', d => (['Decision', 'Scenario', 'ActionType'].includes(d.type) ? 0.95 : 0.7))
    .attr('stroke', '#0b0f14').attr('stroke-width', 1.5)
  labelSel = nodeSel.select('text').text(d => d.label).attr('x', d => radius(d) + 4)
  nodeSel.select('title').remove()
  nodeSel.append('title').text(d => `${d.label}\n[${d.layer} · ${d.type}]${d.value ? '\n값: ' + d.value : ''}\n${d.description || ''}`)

  nodeSel.on('click', (ev, d) => {
    ev.stopPropagation()
    if (ev.shiftKey || linkMode.value) {
      if (!linkFrom) { linkFrom = d.id; linkMode.value = true; selectNode(d); return }
      if (linkFrom !== d.id) {
        const pred = window.prompt(`'${nodeLabel(linkFrom)}' → '${d.label}' 관계명`, 'relatedTo')
        if (pred !== null) addEdge(linkFrom, d.id, pred.trim() || 'relatedTo')
      }
      linkFrom = null; linkMode.value = false
      return
    }
    selectNode(d)
  })
  nodeSel.call(d3.drag()
    .on('start', (ev, d) => { if (!ev.active) sim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y })
    .on('drag', (ev, d) => { d.fx = ev.x; d.fy = ev.y })
    .on('end', (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null }))

  if (sim) sim.stop()
  sim = d3.forceSimulation(simNodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(90).strength(0.5))
    .force('charge', d3.forceManyBody().strength(-260))
    .force('x', d3.forceX(d => bandX[d.layer]).strength(0.12))
    .force('y', d3.forceY(height / 2).strength(0.06))
    .force('collide', d3.forceCollide(d => radius(d) + 14))
    .on('tick', () => {
      linkSel.attr('x1', d => d.source.x).attr('y1', d => d.source.y).attr('x2', d => d.target.x).attr('y2', d => d.target.y)
      elabels.attr('x', d => (d.source.x + d.target.x) / 2).attr('y', d => (d.source.y + d.target.y) / 2)
      nodeSel.attr('transform', d => `translate(${d.x},${d.y})`)
    })
  const fresh = simNodes.every(n => n.x == null) || old.size === 0
  sim.alpha(fresh ? 1 : 0.35).restart()
  highlight()
}

function highlight() {
  if (!nodeSel) return
  const sid = selNode.value?.id
  const e = selEdge.value
  const near = new Set()
  if (sid) for (const x of relatedEdges(sid)) { near.add(idOf(x.source)); near.add(idOf(x.target)) }
  nodeSel.select('circle')
    .attr('stroke', d => (d.id === sid || (e && (idOf(e.source) === d.id || idOf(e.target) === d.id)) ? '#fff' : '#0b0f14'))
    .attr('stroke-width', d => (d.id === sid ? 3 : 1.5))
  nodeSel.style('opacity', d => (!sid || d.id === sid || near.has(d.id) ? 1 : 0.28))
  linkSel?.attr('stroke-opacity', d => (d === e ? 1 : (!sid || idOf(d.source) === sid || idOf(d.target) === sid ? 0.6 : 0.1)))
    .attr('stroke-width', d => (d === e ? 3 : 1.4))
}

watch(layerOn, () => nextTick(renderGraph))
let ro = null
onMounted(() => {
  load(false)
  ro = new ResizeObserver(() => { if (kg.value && svgSel) { const { width, height } = size(); svgSel.attr('viewBox', [0, 0, width, height]) } })
  if (canvasEl.value) ro.observe(canvasEl.value)
})
onBeforeUnmount(() => { sim?.stop(); ro?.disconnect(); stop(); if (audioUrl) URL.revokeObjectURL(audioUrl) })
watch(canvasEl, (el) => { if (el && ro) ro.observe(el) })

// ─────────────────────────────────────────── 음성
const voice = ref('eve')
const voiceBusy = ref(false)
const playing = ref(false)
const paused = ref(false)
const voiceErr = ref('')
let audio = null, audioUrl = null, audioKey = '', usingBrowserTts = false

const voiceStatus = computed(() => {
  if (voiceErr.value) return voiceErr.value
  if (voiceBusy.value) return 'xAI 음성으로 합성하는 중… (10~30초)'
  if (playing.value) return '재생 중'
  if (paused.value) return '일시정지'
  return `약 ${Math.max(1, Math.round((kg.value?.spoken_summary || '').length / 6.5))}초 분량 · 한국어 자연 음성`
})
watch(voice, () => resetAudio())

function resetAudio() {
  stop()
  if (audioUrl) URL.revokeObjectURL(audioUrl)
  audio = null; audioUrl = null; audioKey = ''; voiceErr.value = ''
}

async function ensureAudio() {
  const text = kg.value?.spoken_summary || ''
  const key = voice.value + '|' + text
  if (audio && audioKey === key) return true
  voiceBusy.value = true
  voiceErr.value = ''
  try {
    const blob = await synthesizeSpeech(text, voice.value, props.reportId)
    if (audioUrl) URL.revokeObjectURL(audioUrl)
    audioUrl = URL.createObjectURL(blob)
    audio = new Audio(audioUrl)
    audio.onended = () => { playing.value = false; paused.value = false }
    audio.onpause = () => { playing.value = false }
    audio.onplay = () => { playing.value = true; paused.value = false }
    audioKey = key
    usingBrowserTts = false
    return true
  } catch (e) {
    voiceErr.value = `xAI 음성 실패 → 브라우저 음성으로 재생합니다 (${e.message})`
    usingBrowserTts = true
    return false
  } finally {
    voiceBusy.value = false
  }
}

async function play() {
  if (playing.value) {
    if (usingBrowserTts) { window.speechSynthesis.pause(); playing.value = false; paused.value = true } else audio?.pause()
    paused.value = true
    return
  }
  if (paused.value) {
    if (usingBrowserTts) { window.speechSynthesis.resume(); playing.value = true; paused.value = false } else audio?.play()
    return
  }
  const ok = await ensureAudio()
  if (ok) { audio.currentTime = 0; audio.play() } else browserSpeak()
}
function browserSpeak() {
  const u = new SpeechSynthesisUtterance(kg.value?.spoken_summary || '')
  u.lang = 'ko-KR'
  u.rate = 1.02
  const ko = window.speechSynthesis.getVoices().find(v => v.lang?.startsWith('ko'))
  if (ko) u.voice = ko
  u.onend = () => { playing.value = false; paused.value = false }
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(u)
  playing.value = true
}
function stop() {
  if (audio) { audio.pause(); audio.currentTime = 0 }
  if (typeof window !== 'undefined' && window.speechSynthesis) window.speechSynthesis.cancel()
  playing.value = false
  paused.value = false
}
async function download() {
  const ok = await ensureAudio()
  if (!ok || !audioUrl) return
  const a = document.createElement('a')
  a.href = audioUrl
  a.download = `${props.stock?.ticker || 'stock'}_종합의견_${voice.value}.mp3`
  a.click()
}

// ─────────────────────────────────────────── 기타
async function copyTtl() {
  try { await navigator.clipboard.writeText(kg.value.ttl || ''); copied.value = true; setTimeout(() => (copied.value = false), 1500) } catch (e) { /* ignore */ }
}

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const insightHtml = computed(() => {
  const md = kg.value?.insight_report || '_인사이트 보고서가 없습니다._'
  const lines = esc(md).split('\n')
  let html = '', inList = false, inTable = false
  for (let raw of lines) {
    const line = raw.trimEnd()
    const inline = (t) => t.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>')
    if (/^\|.*\|$/.test(line)) {
      if (/^\|[\s:-|]+\|$/.test(line)) continue
      if (!inTable) { html += '<table>'; inTable = true }
      html += '<tr>' + line.slice(1, -1).split('|').map(c => `<td>${inline(c.trim())}</td>`).join('') + '</tr>'
      continue
    } else if (inTable) { html += '</table>'; inTable = false }
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)$/)
    if (li) { if (!inList) { html += '<ul>'; inList = true } html += `<li>${inline(li[1])}</li>`; continue }
    if (inList) { html += '</ul>'; inList = false }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) { html += `<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`; continue }
    if (line.trim()) html += `<p>${inline(line)}</p>`
  }
  if (inList) html += '</ul>'
  if (inTable) html += '</table>'
  return html
})
</script>

<style scoped>
.kg-step { display: flex; flex-direction: column; gap: 14px; }
.kg-toolbar { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px; }
.kg-layers, .kg-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.layer-chip {
  display: inline-flex; align-items: center; gap: 7px; padding: 6px 12px; border-radius: 999px;
  border: 1px solid var(--c); background: color-mix(in srgb, var(--c) 14%, transparent); color: #e2e8f0;
  font-size: 0.8rem; cursor: pointer; font-family: inherit;
}
.layer-chip .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--c); }
.layer-chip b { color: var(--c); font-family: 'JetBrains Mono', monospace; }
.layer-chip.off { opacity: 0.35; background: transparent; }
.btn {
  border-radius: 8px; padding: 8px 14px; font-size: 0.82rem; font-weight: 600; cursor: pointer;
  font-family: inherit; border: 1px solid transparent; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;
}
.btn:disabled, .btn.disabled { opacity: 0.4; cursor: not-allowed; pointer-events: none; }
.btn.primary { background: #3b82f6; color: #fff; }
.btn.primary:hover { background: #2563eb; }
.btn.ghost { background: transparent; border-color: #2a3041; color: #c9d4e0; }
.btn.ghost:hover { border-color: #3b82f6; color: #fff; }
.btn.danger { background: transparent; border-color: #ef4444; color: #fca5a5; }
.btn.sm { padding: 5px 10px; font-size: 0.76rem; }
.btn.lg { padding: 12px 22px; font-size: 0.92rem; }
.btn.full { width: 100%; justify-content: center; margin-top: 6px; }

.kg-loading { display: flex; gap: 16px; align-items: center; padding: 28px; background: #0f1419; border: 1px solid #2a3041; border-radius: 10px; }
.kg-loading-title { color: #e2e8f0; font-weight: 700; }
.kg-loading-sub { color: #8892a4; font-size: 0.83rem; margin-top: 4px; line-height: 1.6; }
.spinner { width: 28px; height: 28px; border: 3px solid rgba(59,130,246,.2); border-top-color: #3b82f6; border-radius: 50%; animation: spin .8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.kg-error { color: #fca5a5; background: rgba(239,68,68,.08); border: 1px solid rgba(239,68,68,.3); padding: 10px 14px; border-radius: 8px; font-size: 0.85rem; }
.kg-stats { font-size: 0.8rem; color: #a0aec0; display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.tag.live { background: rgba(59,130,246,.15); color: #93c5fd; animation: live 1.4s infinite; }
@keyframes live { 50% { opacity: .5; } }
.view-toggle { margin-left: auto; display: inline-flex; border: 1px solid #2a3041; border-radius: 8px; overflow: hidden; }
.view-toggle button { background: transparent; border: none; color: #8892a4; padding: 6px 12px; font-size: .76rem; cursor: pointer; font-family: inherit; }
.view-toggle button + button { border-left: 1px solid #2a3041; }
.view-toggle button.on { background: #1e3a8a; color: #fff; }
.spo-table { position: absolute; inset: 0; display: flex; flex-direction: column; background: #0b0f14; }
.spo-head { display: flex; gap: 10px; align-items: center; padding: 10px 12px; border-bottom: 1px solid #1e2533; font-size: .76rem; }
.spo-head input { flex: 1; background: #0f1419; border: 1px solid #2a3041; color: #e2e8f0; border-radius: 6px; padding: 6px 10px; font-family: inherit; }
.spo-scroll { overflow: auto; flex: 1; }
.spo-table table { width: 100%; border-collapse: collapse; font-size: .78rem; }
.spo-table th { position: sticky; top: 0; background: #0f1419; color: #8892a4; text-align: left; font-weight: 500; padding: 7px 10px; border-bottom: 1px solid #2a3041; }
.spo-table td { padding: 6px 10px; border-bottom: 1px solid #141b26; color: #c9d4e0; cursor: pointer; }
.spo-table td i { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.spo-table td.pred { color: #fbbf24; font-family: 'JetBrains Mono', monospace; }
.spo-table tr:hover td, .spo-table tr.sel td { background: #111a27; }
.kg-stats b { color: #e2e8f0; }
.muted { color: #8892a4; }
.small { font-size: 0.74rem; line-height: 1.5; }
.tag { margin-left: 8px; padding: 1px 8px; border-radius: 999px; background: rgba(245,158,11,.15); color: #fbbf24; font-size: 0.72rem; }

.kg-main { display: grid; grid-template-columns: minmax(0, 1fr) 290px; gap: 12px; }
.kg-canvas { position: relative; height: 560px; background: radial-gradient(circle at 50% 40%, #121a24, #0b0f14); border: 1px solid #2a3041; border-radius: 10px; overflow: hidden; }
.kg-canvas svg { width: 100%; height: 100%; display: block; }
.kg-hint { position: absolute; left: 10px; bottom: 8px; right: 10px; font-size: 0.7rem; color: #64748b; pointer-events: none; }
.kg-hint b.on { color: #fbbf24; }
.kg-canvas :deep(.nlabel) { fill: #e2e8f0; font-size: 11px; paint-order: stroke; stroke: #0b0f14; stroke-width: 3px; pointer-events: none; }
.kg-canvas :deep(.elabel) { fill: #64748b; font-size: 9px; text-anchor: middle; pointer-events: none; font-family: 'JetBrains Mono', monospace; }

.kg-panel { background: #0f1419; border: 1px solid #2a3041; border-radius: 10px; padding: 14px; overflow-y: auto; max-height: 560px; }
.panel-box { display: flex; flex-direction: column; gap: 8px; }
.panel-title { font-size: 0.85rem; font-weight: 700; color: #e2e8f0; }
.panel-title.mt { margin-top: 14px; padding-top: 12px; border-top: 1px solid #2a3041; }
.panel-sub { font-size: 0.72rem; color: #8892a4; margin-top: 6px; }
.panel-box label { display: flex; flex-direction: column; gap: 3px; font-size: 0.72rem; color: #8892a4; }
.panel-box input, .panel-box select, .panel-box textarea, .spoken textarea, .cq-item textarea, .adr-card textarea, .adr-card input, .voice-ctrl select {
  background: #0b0f14; border: 1px solid #2a3041; color: #e2e8f0; border-radius: 6px; padding: 6px 8px; font-size: 0.8rem; font-family: inherit; width: 100%; box-sizing: border-box;
}
.panel-box textarea, .spoken textarea { resize: vertical; }
.row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.panel-btns { display: flex; gap: 6px; flex-wrap: wrap; }
.edge-desc { font-size: 0.8rem; color: #c9d4e0; }
.rel-list { list-style: none; padding: 0; margin: 0; font-size: 0.75rem; color: #c9d4e0; }
.rel-list li { padding: 4px 0; border-bottom: 1px dashed #1e2533; cursor: pointer; }
.rel-list li i { color: #fbbf24; font-style: normal; font-family: 'JetBrains Mono', monospace; font-size: 0.7rem; margin: 0 4px; }

.voice-bar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px; padding: 14px 18px; background: linear-gradient(90deg, rgba(59,130,246,.12), rgba(16,185,129,.08)); border: 1px solid #2a3041; border-radius: 10px; }
.voice-left { display: flex; gap: 12px; align-items: center; }
.voice-icon { font-size: 1.4rem; }
.voice-title { color: #e2e8f0; font-weight: 700; font-size: 0.9rem; }
.voice-sub { color: #8892a4; font-size: 0.76rem; margin-top: 2px; }
.voice-ctrl { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.voice-ctrl select { width: auto; }
.spoken summary { color: #8892a4; font-size: 0.78rem; cursor: pointer; }
.spoken textarea { margin-top: 8px; line-height: 1.7; }

.kg-tabs { display: flex; gap: 4px; border-bottom: 1px solid #2a3041; flex-wrap: wrap; }
.kg-tabs button { background: none; border: none; color: #8892a4; padding: 10px 14px; font-size: 0.85rem; cursor: pointer; border-bottom: 2px solid transparent; font-family: inherit; }
.kg-tabs button.active { color: #e2e8f0; border-bottom-color: #3b82f6; }
.kg-tabs .cnt { margin-left: 6px; font-size: 0.7rem; color: #3b82f6; }
.tab-body { background: #0f1419; border: 1px solid #2a3041; border-radius: 10px; padding: 18px 22px; }
.md { color: #c9d4e0; line-height: 1.8; font-size: 0.92rem; }
.md :deep(h2) { color: #e2e8f0; font-size: 1.15rem; margin: 18px 0 8px; }
.md :deep(h3), .md :deep(h4), .md :deep(h5) { color: #e2e8f0; font-size: 1rem; margin: 14px 0 6px; }
.md :deep(strong) { color: #fff; }
.md :deep(table) { border-collapse: collapse; margin: 8px 0; font-size: 0.85rem; }
.md :deep(td) { border: 1px solid #2a3041; padding: 5px 10px; }
.md :deep(ul) { padding-left: 20px; margin: 6px 0; }

.cq-item { border-left: 3px solid var(--c); padding: 8px 12px; margin-bottom: 12px; background: #0b0f14; border-radius: 0 8px 8px 0; }
.cq-head { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.badge { font-family: 'JetBrains Mono', monospace; font-size: 0.72rem; background: #1e2533; color: #93c5fd; padding: 2px 8px; border-radius: 4px; }
.layer-tag { font-size: 0.72rem; color: var(--c); }
.cq-q { font-weight: 600; margin-bottom: 6px; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.chip { font-size: 0.7rem; padding: 2px 8px; border-radius: 999px; background: #1e2533; color: #a0aec0; cursor: pointer; }
.chip:hover { color: #fff; background: #2a3041; }

.adr-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.adr-card { background: #0b0f14; border: 1px solid #2a3041; border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.adr-head { display: flex; gap: 8px; align-items: center; }
.adr-status { width: auto !important; }
.adr-status.st-채택 { color: #10b981; } .adr-status.st-보류 { color: #f59e0b; } .adr-status.st-기각 { color: #ef4444; }
.adr-conf { margin-left: auto; font-size: 0.72rem; color: #8892a4; display: flex; align-items: center; gap: 4px; }
.adr-conf input { width: 56px !important; }
.adr-title { font-weight: 700; }
.adr-card dl { margin: 0; display: grid; grid-template-columns: 38px 1fr; gap: 6px; font-size: 0.78rem; }
.adr-card dt { color: #8892a4; padding-top: 6px; }
.adr-card dd { margin: 0; }
.ttl-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 0.78rem; }
.ttl { max-height: 460px; overflow: auto; font-size: 0.74rem; line-height: 1.55; color: #a5f3fc; background: #0b0f14; padding: 14px; border-radius: 8px; margin: 0; white-space: pre; }

.step-nav { display: flex; justify-content: space-between; margin-top: 6px; }

@media (max-width: 900px) {
  .kg-main { grid-template-columns: 1fr; }
  .kg-canvas { height: 440px; }
  .kg-panel { max-height: none; }
}
.node-src { display: inline-block; margin: 4px 0 8px; font-size: 12px; color: #93c5fd; text-decoration: none; }
.node-src:hover { text-decoration: underline; }
</style>
