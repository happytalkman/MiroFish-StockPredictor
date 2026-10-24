import { reactive } from 'vue'
import { getTask } from '../api/stock.js'

// 진행 팝업 상태 + 실제 작업 로그 관리
export function useProgress() {
  const pm = reactive({
    show: false, status: 'running', message: '', progress: null, logs: [],
    startedAt: 0, error: '', stages: []
  })
  let seq = 0
  const stamp = () => { const d = new Date(); return [d.getHours(), d.getMinutes(), d.getSeconds()].map(x => String(x).padStart(2, '0')).join(':') }

  function start(stages = []) {
    Object.assign(pm, { show: true, status: 'running', message: '', progress: null, logs: [], startedAt: Date.now(), error: '', stages })
  }
  // 같은 작업의 실시간 수치 갱신(" — " 앞부분이 같음)은 마지막 줄을 덮어써 로그가 넘치지 않게 함
  function log(text, kind = '') {
    if (!text) return
    pm.message = text
    const base = text.split(' — ')[0]
    const last = pm.logs[pm.logs.length - 1]
    if (last && last.base === base) {
      last.text = text
      last.t = stamp()
      if (kind) last.kind = kind
      return
    }
    pm.logs.push({ id: ++seq, t: stamp(), text, base, kind })
    if (pm.logs.length > 80) pm.logs.splice(0, pm.logs.length - 80)
  }
  function stage(key, status, note) {
    const s = pm.stages.find(x => x.key === key)
    if (s) { s.status = status; if (note !== undefined) s.note = note }
  }
  function done(text) { if (text) log(text, 'ok'); pm.progress = 100; pm.status = 'done' }
  function fail(err) { pm.error = String(err || '오류'); pm.status = 'error'; log(pm.error, 'err') }
  function close() { pm.show = false }

  // 백엔드 task 진행을 따라감: SSE(서버 푸시) 우선, 끊기거나 미지원이면 폴링으로 자동 전환
  function follow(taskId, { interval = 1000, map = (p) => p, onUpdate, sse = true } = {}) {
    return new Promise((resolve, reject) => {
      let finished = false
      const apply = (t) => {
        if (finished || !t) return
        if (t.message) log(t.message)
        if (typeof t.progress === 'number') pm.progress = map(t.progress)
        onUpdate && onUpdate(t)
        if (t.status === 'completed') { finished = true; resolve(t) }
        else if (t.status === 'failed') { finished = true; reject(new Error(t.error || t.message || '작업 실패')) }
      }

      const poll = () => {
        let misses = 0
        const tick = async () => {
          if (finished) return
          try {
            const r = await getTask(taskId)
            misses = 0
            apply(r.data || {})
          } catch (e) {
            if (++misses >= 8) { finished = true; return reject(new Error('진행 상태를 확인할 수 없습니다 (서버 연결 확인)')) }
          }
          if (!finished) setTimeout(tick, interval)
        }
        tick()
      }

      if (!sse || typeof window === 'undefined' || !window.EventSource) return poll()
      let es
      let gotData = false
      try {
        es = new EventSource(`/api/stock/task/${encodeURIComponent(taskId)}/stream`)
      } catch (e) { return poll() }
      const fallback = () => {
        try { es && es.close() } catch (e) { /* ignore */ }
        if (!finished) poll()
      }
      // 서버가 SSE 를 지원하지 않거나(구버전) 3초 안에 첫 데이터가 없으면 폴링으로 전환
      const guard = setTimeout(() => { if (!gotData) fallback() }, 3000)
      es.onmessage = (ev) => {
        gotData = true
        let t
        try { t = JSON.parse(ev.data) } catch (e) { return }
        apply(t)
        if (finished) { clearTimeout(guard); es.close() }
      }
      es.addEventListener('missing', () => { clearTimeout(guard); fallback() })
      es.onerror = () => {
        clearTimeout(guard)
        if (!finished) fallback()
      }
    })
  }

  return { pm, start, log, stage, done, fail, close, follow }
}
