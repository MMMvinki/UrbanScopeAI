// 前端 SSE 解析逻辑冒烟：用与 apps/web/src/lib/api.ts 的 executePlanStream
// 完全相同的帧解析（readable stream + 行切分 + envelope 展开）打真实 /agent/execute，
// 验证真流式帧到达、execution/stream_end 都能收到。Node 18+（自带 fetch）。
const BASE = 'http://127.0.0.1:8010'

async function plan(question) {
  const r = await fetch(`${BASE}/agent/plan`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  })
  if (!r.ok) throw new Error('plan ' + r.status)
  return r.json()
}

async function executePlanStream(plan, question, onEvent) {
  const { intent_id, summary, operation, targets, distance, target_distances, required_data, plan: steps, data_check } = plan
  const response = await fetch(`${BASE}/agent/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({ intent_id, summary, operation, targets, distance, target_distances, required_data, plan: steps, data_check, question, history: [] }),
  })
  if (!response.ok) throw new Error('execute ' + response.status)
  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let eventType = ''
  let dataLines = []
  const dispatch = () => {
    if (!eventType) return
    let envelope = {}
    try { envelope = JSON.parse(dataLines.join('\n')) } catch { return }
    onEvent({ type: eventType, data: envelope.data ?? {}, run_id: envelope.run_id })
    eventType = ''
    dataLines = []
  }
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, idx).replace(/\r$/, '')
      buffer = buffer.slice(idx + 1)
      if (line === '') dispatch()
      else if (line.startsWith('event: ')) eventType = line.slice(7)
      else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
    }
  }
  dispatch()
}

async function run(label, question) {
  console.log(`\n==== ${label} — ${question}`)
  const p = await plan(question)
  console.log(`[plan] op=${p.operation} targets=${JSON.stringify(p.targets)}`)
  const seen = {}
  let execData = null
  let streamEnd = null
  let toolStarts = 0
  await executePlanStream(p, question, (ev) => {
    seen[ev.type] = (seen[ev.type] ?? 0) + 1
    if (ev.type === 'execution') execData = ev.data
    if (ev.type === 'stream_end') streamEnd = ev.data
    if (ev.type === 'tool_start') toolStarts++
  })
  const checks = []
  const ok = (label2, cond) => { checks.push([label2, cond]) }
  ok('thought_delta >10 帧（真流式）', (seen.thought_delta ?? 0) > 10)
  ok('tool_start ≥1', toolStarts >= 1)
  ok('execution 收到', !!execData)
  ok('stream_end 收到（修复后不再丢尾帧）', !!streamEnd)
  ok('stream_end.status=success', streamEnd?.status === 'success')
  ok('execution 有 trace', Array.isArray(execData?.trace) && execData.trace.length > 0)
  ok('execution.used_fallback=false', execData?.used_fallback === false)
  let allPass = true
  for (const [name, cond] of checks) {
    if (!cond) allPass = false
    console.log(`[${cond ? 'PASS' : 'FAIL'}] ${name}`)
  }
  return allPass
}

let globalOk = true
for (const [label, q] of [
  ['学校覆盖', '分析学校覆盖范围'],
  ['城市体检', '帮我对这个片区做一次街区城市体检'],
]) {
  const pass = await run(label, q).catch((e) => { console.log('ERROR', e.message); return false })
  if (!pass) globalOk = false
}
console.log('\n=== 前端 SSE 解析冒烟:', globalOk ? '全部通过' : '存在失败项', '===')
process.exit(globalOk ? 0 : 1)
