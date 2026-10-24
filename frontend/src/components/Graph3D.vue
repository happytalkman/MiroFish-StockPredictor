<template>
  <div class="g3-wrap" ref="wrapEl">
    <canvas ref="canvasEl" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointerleave="onLeave" @wheel.prevent="onWheel"></canvas>
    <div v-if="loading" class="g3-overlay"><div class="g3-spin"></div>three.js 3D 엔진을 불러오는 중…</div>
    <div v-if="err" class="g3-overlay err">3D 라이브러리(three.js)를 불러오지 못했습니다. 인터넷 연결을 확인하세요.<br /><small>{{ err }}</small></div>
    <div class="g3-legend">
      <span v-for="l in LAYERS" :key="l.key"><i :style="{ background: l.color }"></i>{{ l.floor }}</span>
    </div>
    <div class="g3-ctrl">
      <button :class="{ on: autoRotate }" @click="autoRotate = !autoRotate">{{ autoRotate ? '⟳ 자동회전 켜짐' : '⟳ 자동회전' }}</button>
      <button @click="resetView">⌂ 시점 초기화</button>
    </div>
    <div v-if="hover" class="g3-tip" :style="{ left: tipX + 'px', top: tipY + 'px' }">
      <b>{{ hover.label }}</b><span>{{ hover.layer }} · {{ hover.type }}</span><em v-if="hover.value">{{ hover.value }}</em>
    </div>
    <div class="g3-hint">드래그: 회전 · 휠: 확대/축소 · 클릭: 노드 선택(오른쪽 패널에서 편집) · 층: Semantic(아래) → Kinetic → Dynamic(위)</div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import * as d3 from 'd3'

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  edges: { type: Array, default: () => [] },
  layerOn: { type: Object, default: () => ({ semantic: true, kinetic: true, dynamic: true }) },
  selectedId: { type: String, default: null },
  version: { type: Number, default: 0 }
})
const emit = defineEmits(['select'])

const THREE_URL = 'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js'
const LAYERS = [
  { key: 'semantic', color: '#3b82f6', y: -150, floor: 'Semantic 의미 (1층)' },
  { key: 'kinetic', color: '#f59e0b', y: 0, floor: 'Kinetic 행위 (2층)' },
  { key: 'dynamic', color: '#10b981', y: 150, floor: 'Dynamic 변화·결정 (3층)' }
]
const LY = Object.fromEntries(LAYERS.map(l => [l.key, l]))

const wrapEl = ref(null)
const canvasEl = ref(null)
const loading = ref(true)
const err = ref('')
const autoRotate = ref(true)
const hover = ref(null)
const tipX = ref(0)
const tipY = ref(0)

let THREE = null, renderer = null, scene = null, camera = null, raf = 0, ro = null
let graphGroup = null, nodeMeshes = [], labelSprites = [], edgeLines = null, edgeLabelGroup = null
let cam = { theta: 0.8, phi: 1.15, r: 720, target: null }
let drag = null
const idOf = (x) => (typeof x === 'object' && x ? x.id : x)

async function init() {
  try {
    THREE = await import(/* @vite-ignore */ THREE_URL)
  } catch (e) {
    err.value = String(e?.message || e)
    loading.value = false
    return
  }
  renderer = new THREE.WebGLRenderer({ canvas: canvasEl.value, antialias: true, alpha: false })
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1))
  renderer.setClearColor(0x0b0f14, 1)
  scene = new THREE.Scene()
  scene.fog = new THREE.Fog(0x0b0f14, 900, 2200)
  camera = new THREE.PerspectiveCamera(50, 1, 1, 5000)
  cam.target = new THREE.Vector3(0, 0, 0)
  scene.add(new THREE.AmbientLight(0xffffff, 0.55))
  const dl = new THREE.DirectionalLight(0xffffff, 0.9)
  dl.position.set(300, 500, 400)
  scene.add(dl)
  const pl = new THREE.PointLight(0x60a5fa, 0.8, 1600)
  pl.position.set(-300, 200, -200)
  scene.add(pl)
  // 층 바닥판
  for (const l of LAYERS) {
    const g = new THREE.RingGeometry(40, 380, 64)
    const m = new THREE.MeshBasicMaterial({ color: l.color, transparent: true, opacity: 0.06, side: THREE.DoubleSide, depthWrite: false })
    const disc = new THREE.Mesh(g, m)
    disc.rotation.x = -Math.PI / 2
    disc.position.y = l.y
    scene.add(disc)
    const grid = new THREE.PolarGridHelper(380, 12, 5, 64, l.color, l.color)
    grid.position.y = l.y
    grid.material.transparent = true
    grid.material.opacity = 0.18
    scene.add(grid)
    const tag = textSprite(l.floor.split(' (')[0], l.color, 30, true)
    tag.position.set(-420, l.y + 14, 0)
    scene.add(tag)
  }
  resize()
  ro = new ResizeObserver(resize)
  ro.observe(wrapEl.value)
  build()
  loading.value = false
  loop()
}

function textSprite(text, color = '#e2e8f0', size = 22, bold = false) {
  const pad = 8
  const c = document.createElement('canvas')
  const ctx = c.getContext('2d')
  const font = `${bold ? 800 : 600} ${size}px 'Noto Sans KR', 'Inter', sans-serif`
  ctx.font = font
  const w = Math.ceil(ctx.measureText(text).width) + pad * 2
  const h = size + pad * 2
  c.width = w * 2
  c.height = h * 2
  ctx.scale(2, 2)
  ctx.font = font
  ctx.fillStyle = 'rgba(11,15,20,0.72)'
  const r = 8
  ctx.beginPath()
  ctx.moveTo(r, 0); ctx.arcTo(w, 0, w, h, r); ctx.arcTo(w, h, 0, h, r); ctx.arcTo(0, h, 0, 0, r); ctx.arcTo(0, 0, w, 0, r)
  ctx.fill()
  ctx.fillStyle = color
  ctx.textBaseline = 'middle'
  ctx.fillText(text, pad, h / 2 + 1)
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }))
  sp.scale.set(w * 0.62, h * 0.62, 1)
  sp.userData.dispose = () => { tex.dispose(); sp.material.dispose() }
  return sp
}

function clearGraph() {
  if (!graphGroup) return
  graphGroup.traverse(o => {
    if (o.geometry && !o.isSprite) o.geometry.dispose()
    if (o.userData?.dispose) o.userData.dispose()
    else if (o.material && !o.isSprite) o.material.dispose()
  })
  scene.remove(graphGroup)
  graphGroup = null
  nodeMeshes = []
  labelSprites = []
  edgeLines = null
  edgeLabelGroup = null
}

function build() {
  if (!THREE || !scene) return
  clearGraph()
  graphGroup = new THREE.Group()
  scene.add(graphGroup)
  const visible = props.nodes.filter(n => props.layerOn[n.layer] !== false)
  const vis = new Set(visible.map(n => n.id))
  const links = props.edges
    .map(e => ({ s: idOf(e.source), t: idOf(e.target), p: e.predicate, layer: e.layer }))
    .filter(e => vis.has(e.s) && vis.has(e.t))
  // 층(y)은 고정, 수평면(x,z) 배치는 d3-force 로 계산
  const simNodes = visible.map(n => ({ id: n.id }))
  const sim = d3.forceSimulation(simNodes)
    .force('link', d3.forceLink(links.map(l => ({ source: l.s, target: l.t }))).id(d => d.id).distance(95).strength(0.4))
    .force('charge', d3.forceManyBody().strength(-220))
    .force('center', d3.forceCenter(0, 0))
    .force('collide', d3.forceCollide(30))
    .stop()
  sim.tick(260)
  const pos = new Map(simNodes.map(n => [n.id, n]))
  const deg = {}
  for (const l of links) { deg[l.s] = (deg[l.s] || 0) + 1; deg[l.t] = (deg[l.t] || 0) + 1 }

  const P = new Map()
  for (const n of visible) {
    const p = pos.get(n.id)
    const layer = LY[n.layer] || LY.semantic
    const v = new THREE.Vector3(p.x * 1.1, layer.y + ((n.id.charCodeAt(0) % 7) - 3) * 4, p.y * 1.1)
    P.set(n.id, v)
    const radius = 7 + Math.min(12, (deg[n.id] || 0) * 1.4)
    const key = ['Decision', 'Scenario', 'ActionType'].includes(n.type)
    const geo = key ? new THREE.OctahedronGeometry(radius * 1.15) : new THREE.SphereGeometry(radius, 24, 18)
    const mat = new THREE.MeshStandardMaterial({ color: layer.color, emissive: layer.color, emissiveIntensity: 0.28, roughness: 0.35, metalness: 0.25 })
    const mesh = new THREE.Mesh(geo, mat)
    mesh.position.copy(v)
    mesh.userData = { node: n, radius }
    graphGroup.add(mesh)
    nodeMeshes.push(mesh)
    const lab = textSprite(n.label, '#e2e8f0', 18)
    lab.position.set(v.x, v.y + radius + 14, v.z)
    lab.userData.nodeId = n.id
    graphGroup.add(lab)
    labelSprites.push(lab)
  }
  // 엣지: 출발색 → 도착색 그라데이션 선
  const positions = [], colors = []
  const cA = new THREE.Color(), cB = new THREE.Color()
  for (const l of links) {
    const a = P.get(l.s), b = P.get(l.t)
    positions.push(a.x, a.y, a.z, b.x, b.y, b.z)
    const na = visible.find(n => n.id === l.s), nb = visible.find(n => n.id === l.t)
    cA.set((LY[na?.layer] || LY.semantic).color)
    cB.set((LY[nb?.layer] || LY.semantic).color)
    colors.push(cA.r, cA.g, cA.b, cB.r, cB.g, cB.b)
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  g.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))
  edgeLines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.55 }))
  edgeLines.userData.links = links
  edgeLines.userData.P = P
  graphGroup.add(edgeLines)
  // 방향 표시: 도착 쪽 작은 원뿔
  for (const l of links) {
    const a = P.get(l.s), b = P.get(l.t)
    const dir = new THREE.Vector3().subVectors(b, a)
    const len = dir.length()
    if (len < 1) continue
    dir.normalize()
    const cone = new THREE.Mesh(new THREE.ConeGeometry(3.2, 9, 8), new THREE.MeshBasicMaterial({ color: (LY[l.layer] || LY.semantic).color, transparent: true, opacity: 0.8 }))
    cone.position.copy(b).addScaledVector(dir, -16)
    cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir)
    graphGroup.add(cone)
  }
  edgeLabelGroup = new THREE.Group()
  graphGroup.add(edgeLabelGroup)
  applySelection()
}

function applySelection() {
  if (!graphGroup) return
  const sid = props.selectedId
  const near = new Set()
  const links = edgeLines?.userData.links || []
  if (sid) for (const l of links) if (l.s === sid || l.t === sid) { near.add(l.s); near.add(l.t) }
  for (const m of nodeMeshes) {
    const on = !sid || near.has(m.userData.node.id) || m.userData.node.id === sid
    m.material.opacity = on ? 1 : 0.18
    m.material.transparent = !on
    m.material.emissiveIntensity = m.userData.node.id === sid ? 0.9 : 0.28
  }
  for (const s of labelSprites) s.material.opacity = !sid || near.has(s.userData.nodeId) || s.userData.nodeId === sid ? 1 : 0.15
  if (edgeLines) edgeLines.material.opacity = sid ? 0.25 : 0.55
  // 선택 노드의 술어(predicate) 라벨 표시
  if (edgeLabelGroup) {
    edgeLabelGroup.children.slice().forEach(c => { c.userData.dispose?.(); edgeLabelGroup.remove(c) })
    if (sid) {
      const P = edgeLines.userData.P
      for (const l of links) {
        if (l.s !== sid && l.t !== sid) continue
        const a = P.get(l.s), b = P.get(l.t)
        const sp = textSprite(l.p, '#fbbf24', 15)
        sp.position.set((a.x + b.x) / 2, (a.y + b.y) / 2 + 6, (a.z + b.z) / 2)
        edgeLabelGroup.add(sp)
      }
    }
  }
}

function resize() {
  if (!renderer || !wrapEl.value) return
  const { width, height } = wrapEl.value.getBoundingClientRect()
  renderer.setSize(width, height, false)
  camera.aspect = width / Math.max(1, height)
  camera.updateProjectionMatrix()
}

function updateCamera() {
  const t = cam.target
  camera.position.set(
    t.x + cam.r * Math.sin(cam.phi) * Math.cos(cam.theta),
    t.y + cam.r * Math.cos(cam.phi),
    t.z + cam.r * Math.sin(cam.phi) * Math.sin(cam.theta)
  )
  camera.lookAt(t)
}

function loop() {
  raf = requestAnimationFrame(loop)
  if (autoRotate.value && !drag) cam.theta += 0.0022
  const t = performance.now() / 1000
  for (const m of nodeMeshes) {
    if (m.userData.node.id === props.selectedId) {
      const s = 1 + Math.sin(t * 4) * 0.12
      m.scale.set(s, s, s)
    } else if (m.scale.x !== 1) m.scale.set(1, 1, 1)
  }
  updateCamera()
  renderer.render(scene, camera)
}

function resetView() { cam = { theta: 0.8, phi: 1.15, r: 720, target: cam.target.set(0, 0, 0) } }

function pick(ev) {
  if (!THREE || !renderer) return null
  const rect = canvasEl.value.getBoundingClientRect()
  const mouse = new THREE.Vector2(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1)
  const rc = new THREE.Raycaster()
  rc.setFromCamera(mouse, camera)
  const hit = rc.intersectObjects(nodeMeshes, false)[0]
  return hit ? hit.object.userData.node : null
}

function onDown(ev) {
  drag = { x: ev.clientX, y: ev.clientY, moved: false }
  canvasEl.value.setPointerCapture?.(ev.pointerId)
}
function onMove(ev) {
  if (drag) {
    const dx = ev.clientX - drag.x, dy = ev.clientY - drag.y
    if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true
    cam.theta += dx * 0.006
    cam.phi = Math.max(0.15, Math.min(Math.PI - 0.15, cam.phi - dy * 0.006))
    drag.x = ev.clientX
    drag.y = ev.clientY
    hover.value = null
    return
  }
  const n = pick(ev)
  hover.value = n
  canvasEl.value.style.cursor = n ? 'pointer' : 'grab'
  if (n) {
    const rect = wrapEl.value.getBoundingClientRect()
    tipX.value = ev.clientX - rect.left + 14
    tipY.value = ev.clientY - rect.top + 10
  }
}
function onUp(ev) {
  const wasDrag = drag?.moved
  drag = null
  if (!wasDrag) {
    const n = pick(ev)
    emit('select', n ? n.id : null)
  }
}
function onLeave() { drag = null; hover.value = null }
function onWheel(ev) { cam.r = Math.max(180, Math.min(2200, cam.r * (ev.deltaY > 0 ? 1.1 : 0.9))) }

watch(() => [props.version, props.nodes.length, props.edges.length, props.layerOn.semantic, props.layerOn.kinetic, props.layerOn.dynamic], () => build())
watch(() => props.selectedId, () => applySelection())

onMounted(init)
onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  ro?.disconnect()
  clearGraph()
  renderer?.dispose()
})
</script>

<style scoped>
.g3-wrap { position: relative; width: 100%; height: 100%; }
canvas { width: 100%; height: 100%; display: block; cursor: grab; touch-action: none; }
.g3-overlay { position: absolute; inset: 0; display: flex; flex-direction: column; gap: 10px; align-items: center; justify-content: center; color: #94a3b8; font-size: .85rem; text-align: center; padding: 20px; }
.g3-overlay.err { color: #fca5a5; }
.g3-spin { width: 26px; height: 26px; border: 3px solid rgba(59,130,246,.2); border-top-color: #3b82f6; border-radius: 50%; animation: g3s .8s linear infinite; }
@keyframes g3s { to { transform: rotate(360deg); } }
.g3-legend { position: absolute; top: 10px; left: 10px; display: flex; flex-direction: column; gap: 4px; font-size: .7rem; color: #c9d4e0; background: rgba(11,15,20,.7); padding: 8px 10px; border-radius: 8px; border: 1px solid #1e2533; pointer-events: none; }
.g3-legend i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 6px; }
.g3-ctrl { position: absolute; top: 10px; right: 10px; display: flex; gap: 6px; }
.g3-ctrl button { background: rgba(11,15,20,.8); border: 1px solid #2a3041; color: #c9d4e0; border-radius: 6px; padding: 5px 9px; font-size: .72rem; cursor: pointer; font-family: inherit; }
.g3-ctrl button.on { border-color: #3b82f6; color: #93c5fd; }
.g3-tip { position: absolute; pointer-events: none; background: rgba(11,15,20,.92); border: 1px solid #2a3041; border-radius: 8px; padding: 7px 10px; display: flex; flex-direction: column; gap: 2px; font-size: .74rem; color: #94a3b8; max-width: 260px; }
.g3-tip b { color: #e2e8f0; font-size: .82rem; }
.g3-tip em { color: #fbbf24; font-style: normal; font-family: 'JetBrains Mono', monospace; }
.g3-hint { position: absolute; left: 10px; right: 10px; bottom: 8px; font-size: .68rem; color: #64748b; pointer-events: none; }
</style>
