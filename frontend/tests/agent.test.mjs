import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { setImmediate } from 'node:timers/promises'
import { runInNewContext } from 'node:vm'
import { randomUUID } from 'node:crypto'
import test from 'node:test'
import ts from 'typescript'
import * as Vue from 'vue'
import { compileScript, compileTemplate, parse } from 'vue/compiler-sfc'

const load = (path, imports = {}, globals = {}) => {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8').replaceAll('import.meta.env.BASE_URL', "'/'")
  const code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText
  const module = { exports: {} }
  runInNewContext(code, { module, exports: module.exports, require: (name) => imports[name], console, Error, ArrayBuffer, Uint8Array, Float32Array, DataView, Date, DOMException, ...globals })
  return module.exports
}

const settle = async () => { await setImmediate(); await setImmediate() }
const action = (state = 'pending') => ({ id: 'action-1', kind: 'cancel_order', state, order_id: 1, order_number: 'ORD-TEST', total: '20.00', reason: '买错了', effect: '不发起资金退款', title: '取消订单', expires_at: new Date(Date.now() + 300000).toISOString() })

function fixture(t, options = {}) {
  let clockNow = Date.now()
  let timerId = 0
  const timers = new Map()
  const sockets = []
  const recordings = []
  const players = []
  const storage = new Map()
  const archiveCalls = []
  const snapshot = { id: 'conversation-1', messages: [], actions: [], tickets: [], busy: false, ...options.snapshot }
  const capabilities = { chat: true, voice_input: true, voice_output: true, knowledge_base: false, ...options.capabilities }
  class ApiError extends Error { constructor(message, status) { super(message); this.status = status } }
  class Socket {
    static OPEN = 1
    readyState = 0
    bufferedAmount = 0
    sent = []
    constructor(url) { this.url = url; sockets.push(this) }
    send(data) { this.sent.push(typeof data === 'string' ? JSON.parse(data) : data) }
    close() { this.readyState = 3; this.onclose?.({}) }
    open() { this.readyState = 1; this.onopen?.(); this.server({ type: 'session.ready', capabilities }) }
    server(data) {
      if (data instanceof ArrayBuffer) { this.onmessage?.({ data }); return }
      if (data.type === 'user.message' || data.type === 'assistant.message') {
        const message = data.message ?? { role: 'user', request_id: data.request_id, text: data.text, created_at: new Date().toISOString() }
        const index = snapshot.messages.findIndex((item) => item.request_id === message.request_id && item.role === message.role)
        if (index >= 0) snapshot.messages[index] = message
        else snapshot.messages.push(message)
      }
      if (data.type === 'turn.done') snapshot.busy = false
      this.onmessage?.({ data: JSON.stringify(data) })
    }
  }
  class Recorder {
    constructor() { this.stops = []; recordings.push(this) }
    async start(callback) { this.callback = callback; return options.permission ? options.permission : true }
    async stop(discard) { this.stops.push(discard); if (!discard) this.callback?.(new ArrayBuffer(320), 0.1) }
    frame() { this.callback?.(new ArrayBuffer(12000), 0.3) }
  }
  class Player {
    constructor(onPlaying) { this.onPlaying = onPlaying; this.chunks = []; players.push(this) }
    async unlock() {}
    start() {}
    push(data) { this.chunks.push(data); this.onPlaying(true); return true }
    stop() { this.onPlaying(false) }
    dispose() { this.stop() }
  }
  class ClockDate extends Date {
    constructor(...args) { super(...(args.length ? args : [clockNow])) }
    static now() { return clockNow }
  }
  const api = {
    AgentApiError: ApiError, agentSocketUrl: (id) => `ws://localhost/api/agent/ws?conversation_id=${id}`,
    fetchAgentStatus: async () => ({ capabilities, limits: { audio_seconds: 60, text_characters: 2000 } }),
    createAgentConversation: options.createConversation ?? (async () => ({ id: snapshot.id })),
    fetchAgentConversation: options.fetchConversation ?? (async () => structuredClone(snapshot)),
    archiveAgentConversation: async (id) => { archiveCalls.push(id); await options.archiveConversation?.(id) },
  }
  const { useAgentChat } = load('../src/features/agent/useAgentChat.ts', { vue: Vue, './api': api, './audio': { PcmRecorder: Recorder, PcmPlayer: Player } }, {
    WebSocket: Socket, crypto: { randomUUID }, Date: ClockDate,
    localStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
    setTimeout: (fn) => { const id = ++timerId; timers.set(id, fn); return id },
    clearTimeout: (id) => timers.delete(id), setInterval: () => ++timerId, clearInterval() {},
  })
  const chat = useAgentChat('test_admin')
  t.after(() => chat.dispose())
  return { chat, sockets, recordings, players, snapshot, timers, storage, archiveCalls, ApiError, advance: (ms) => { clockNow += ms }, async start() { await chat.initialize(); sockets.at(-1).open(); return sockets.at(-1) } }
}

test('removing an inactive recent conversation preserves the active chat and persists removal', async (t) => {
  const f = fixture(t)
  const socket = await f.start()
  f.chat.recentConversations.value.push({ id: 'other-conversation', title: '其他会话' })
  assert.equal(await f.chat.removeConversation('other-conversation'), true)
  assert.deepEqual(f.archiveCalls, ['other-conversation'])
  assert.equal(f.chat.conversationId.value, 'conversation-1')
  assert.equal(socket.readyState, 1)
  assert.equal(f.chat.recentConversations.value.some((item) => item.id === 'other-conversation'), false)
  assert.equal(JSON.parse(f.storage.get('corelystics.agent.test_admin.recent')).some((item) => item.id === 'other-conversation'), false)
})

test('removing the active conversation closes its socket and replaces it with a new empty conversation', async (t) => {
  let creates = 0
  const f = fixture(t, { createConversation: async () => ({ id: ++creates === 1 ? 'conversation-1' : 'conversation-new' }) })
  const oldSocket = await f.start()
  f.chat.messages.value.push({ role: 'user', request_id: 'old-message', text: '旧会话', delivery: 'sent' })
  assert.equal(await f.chat.removeConversation('conversation-1'), true)
  assert.equal(oldSocket.readyState, 3)
  assert.equal(f.chat.conversationId.value, 'conversation-new')
  assert.equal(f.chat.messages.value.length, 0)
  assert.equal(f.chat.recentConversations.value.some((item) => item.id === 'conversation-1'), false)
  assert.equal(f.storage.get('corelystics.agent.test_admin'), 'conversation-new')
})

test('failed archival preserves the conversation and allows retry', async (t) => {
  const f = fixture(t, { archiveConversation: async () => { throw new Error('删除失败，请重试') } })
  const socket = await f.start()
  assert.equal(await f.chat.removeConversation('conversation-1'), false)
  assert.equal(f.chat.removalError.value, '删除失败，请重试')
  assert.equal(f.chat.recentConversations.value.length, 1)
  assert.equal(f.chat.conversationId.value, 'conversation-1')
  assert.equal(socket.readyState, 1)
  assert.equal(f.chat.removingConversation.value, null)
  assert.equal(f.chat.canChat.value, true)
})

test('duplicate archival clicks and sending a new message during deletion cannot race', async (t) => {
  let finish
  const wait = new Promise((resolve) => { finish = resolve })
  const f = fixture(t, { archiveConversation: async () => wait })
  await f.start()
  f.chat.recentConversations.value.push({ id: 'other-conversation', title: '其他会话' })
  const first = f.chat.removeConversation('other-conversation')
  assert.equal(await f.chat.removeConversation('other-conversation'), false)
  assert.equal(f.chat.sendText('新问题'), false)
  assert.equal(f.archiveCalls.length, 1)
  finish()
  assert.equal(await first, true)
})

test('stale archived conversation on restore is pruned instead of coming back in recents', async (t) => {
  let f
  f = fixture(t, {
    fetchConversation: async () => { throw new f.ApiError('会话已删除', 404) },
    createConversation: async () => ({ id: 'conversation-new' }),
  })
  f.storage.set('corelystics.agent.test_admin', 'archived-conversation')
  f.chat.recentConversations.value = [{ id: 'archived-conversation', title: '已删除会话' }]
  await f.start()
  assert.equal(f.chat.conversationId.value, 'conversation-new')
  assert.equal(f.chat.recentConversations.value.some((item) => item.id === 'archived-conversation'), false)
})

test('an active request prevents archival before any HTTP mutation is sent', async (t) => {
  const f = fixture(t)
  await f.start()
  f.chat.sendText('查询订单')
  assert.equal(await f.chat.removeConversation('conversation-1'), false)
  assert.equal(f.archiveCalls.length, 0)
})

test('AgentView remains compilable after formatting and CSS includes standard line-clamp', () => {
  const filename = '../src/views/AgentView.vue'
  const source = readFileSync(new URL(filename, import.meta.url), 'utf8')
  const parsed = parse(source, { filename })
  assert.deepEqual(parsed.errors, [])
  const script = compileScript(parsed.descriptor, { id: 'agent' })
  const template = compileTemplate({ source: parsed.descriptor.template.content, filename, id: 'agent', compilerOptions: { bindingMetadata: script.bindings, expressionPlugins: ['typescript'] } })
  assert.deepEqual(template.errors, [])
  assert.match(source, /(?<!-)\bline-clamp:\s*2/)
})

test('Agent socket URL follows API host, prefix and HTTPS without exposing credentials', () => {
  const { agentSocketUrl } = load('../src/features/agent/api.ts', { '../../shared/api/httpClient': { httpClient: { defaults: { baseURL: 'https://api.example.test/prefix/api/' } } } }, { URL, URLSearchParams, window: { location: { origin: 'https://app.example.test' } } })
  const url = new URL(agentSocketUrl('conversation-1'))
  assert.equal(url.protocol, 'wss:')
  assert.equal(url.host, 'api.example.test')
  assert.equal(url.pathname, '/prefix/api/agent/ws')
  assert.equal(url.searchParams.get('conversation_id'), 'conversation-1')
  assert.equal([...url.searchParams].length, 1)
})

test('text is trimmed, acknowledged without duplication, and completed history is restored', async (t) => {
  const f = fixture(t)
  const socket = await f.start()
  assert(f.chat.sendText('  查询订单  '))
  const event = socket.sent.find((item) => item.type === 'chat.send')
  assert.equal(event.text, '查询订单')
  assert.equal(f.chat.messages.value.length, 1)
  assert.equal(f.chat.messages.value[0].delivery, 'sending')
  socket.server({ type: 'user.message', request_id: event.request_id, text: event.text })
  assert.equal(f.chat.messages.value.length, 1)
  assert.equal(f.chat.messages.value[0].delivery, 'sent')
  socket.server({ type: 'assistant.message', request_id: event.request_id, message: { role: 'assistant', request_id: event.request_id, text: '已查询到订单', created_at: new Date().toISOString() } })
  socket.server({ type: 'turn.done', request_id: event.request_id, status: 'completed' })
  await settle()
  assert.equal(f.chat.messages.value.length, 2)
  assert.equal(f.chat.busy.value, false)
})

test('an interrupted old speech turn cannot clear a new request or play late audio', async (t) => {
  const f = fixture(t)
  const socket = await f.start()
  f.chat.sendText('第一条')
  const first = socket.sent.at(-1).request_id
  socket.server({ type: 'audio.start', request_id: first, sample_rate: 24000 })
  socket.server(new ArrayBuffer(20))
  assert.equal(f.players[0].chunks.length, 1)
  assert(f.chat.sendText('第二条'))
  const second = socket.sent.at(-1).request_id
  assert.notEqual(first, second)
  socket.server({ type: 'turn.done', request_id: first, status: 'cancelled' })
  socket.server({ type: 'audio.start', request_id: first })
  socket.server(new ArrayBuffer(20))
  assert.equal(f.players[0].chunks.length, 1)
  assert.equal(f.chat.busy.value, true)
})

test('release before microphone permission resolves never starts or submits a recording', async (t) => {
  let allow
  const permission = new Promise((resolve) => { allow = resolve })
  const f = fixture(t, { permission })
  const socket = await f.start()
  const start = f.chat.beginRecording()
  await f.chat.finishRecording(true)
  allow(true)
  await start
  assert.equal(socket.sent.filter((event) => event.type === 'audio.start' || event.type === 'audio.commit').length, 0)
  assert.equal(f.chat.preparing.value, false)
  assert.equal(f.chat.endingRecording.value, false)
})

test('audio frames wait for ready and final frame precedes release-to-send commit', async (t) => {
  const f = fixture(t)
  const socket = await f.start()
  const start = f.chat.beginRecording()
  await settle()
  const request = socket.sent.find((event) => event.type === 'audio.start')
  f.recordings[0].frame()
  assert.equal(socket.sent.filter((item) => item instanceof ArrayBuffer).length, 0)
  socket.server({ type: 'audio.ready', request_id: request.request_id })
  await start
  assert.equal(socket.sent.filter((item) => item instanceof ArrayBuffer).length, 1)
  f.advance(1000)
  await f.chat.finishRecording()
  assert(socket.sent.at(-2) instanceof ArrayBuffer)
  assert.equal(socket.sent.at(-2).byteLength, 320)
  assert.equal(socket.sent.at(-1).type, 'audio.commit')
  assert.equal(f.chat.messages.value.length, 0)
})

test('slide cancellation sends cancel only and discards the last microphone frame', async (t) => {
  const f = fixture(t)
  const socket = await f.start()
  const start = f.chat.beginRecording()
  await settle()
  const request = socket.sent.find((event) => event.type === 'audio.start')
  socket.server({ type: 'audio.ready', request_id: request.request_id })
  await start
  f.recordings[0].frame()
  await f.chat.finishRecording(true)
  assert.equal(socket.sent.at(-1).type, 'turn.cancel')
  assert.equal(socket.sent.filter((item) => item.type === 'audio.commit').length, 0)
  assert.equal(f.recordings[0].stops.at(-1), true)
  assert.equal(f.chat.messages.value.length, 0)
})

test('late provider turn.done releases busy status after a failed request', async (t) => {
  const f = fixture(t)
  const socket = await f.start()
  f.chat.sendText('查询订单')
  const requestId = socket.sent.at(-1).request_id
  f.snapshot.busy = true
  socket.server({ type: 'error', request_id: requestId, code: 'provider_busy', message: '请稍后重试' })
  await settle()
  assert.equal(f.chat.remoteBusy.value, true)
  socket.server({ type: 'turn.done', request_id: requestId, status: 'failed' })
  await settle()
  assert.equal(f.chat.remoteBusy.value, false)
  assert.equal(f.chat.canChat.value, true)
})

test('expired actions cannot confirm and reconnect does not replay a confirmed action', async (t) => {
  const pending = action()
  const f = fixture(t, { snapshot: { actions: [pending] } })
  let socket = await f.start()
  f.chat.confirmAction({ ...pending, expires_at: new Date(Date.now() - 1000).toISOString() })
  assert.equal(socket.sent.length, 0)
  f.chat.confirmAction(pending)
  assert.equal(socket.sent.at(-1).type, 'action.confirm')
  assert.equal(socket.sent.at(-1).action_id, pending.id)
  socket.close()
  f.snapshot.actions = [{ ...pending, state: 'completed' }]
  await f.chat.initialize()
  socket = f.sockets.at(-1)
  socket.open()
  assert.equal(f.sockets.flatMap((item) => item.sent).filter((item) => item.type === 'action.confirm').length, 1)
  assert.equal(f.chat.actions.value[0].state, 'completed')
})

test('successful automatic reconnection replaces the stale reconnecting notice', async (t) => {
  const f = fixture(t)
  const socket = await f.start()
  socket.close()
  assert.match(f.chat.notice.value, /正在恢复/)
  const scheduled = [...f.timers.values()].at(-1)
  scheduled()
  await settle()
  f.sockets.at(-1).open()
  assert.equal(f.chat.connection.value, 'online')
  assert.match(f.chat.notice.value, /连接已恢复/)
})

test('disabled model and microphone leave the manual ticket flow available', async (t) => {
  const f = fixture(t, { capabilities: { chat: false, voice_input: false } })
  const socket = await f.start()
  assert.equal(f.chat.sendText('你好'), false)
  await f.chat.beginRecording()
  assert(f.chat.handoff('请人工核实政策'))
  assert.equal(socket.sent.at(-1).type, 'handoff.request')
})

test('PCM playback joins odd-byte network frames without corrupting samples', async () => {
  const sources = []
  class AudioContext {
    state = 'running'
    currentTime = 0
    destination = {}
    createBuffer(channels, length, sampleRate) {
      const samples = new Float32Array(length)
      return { duration: length / sampleRate, samples, getChannelData: () => samples }
    }
    createBufferSource() {
      const source = { connect() {}, disconnect() {}, start() {}, stop() {} }
      sources.push(source)
      return source
    }
    async close() {}
  }
  const { PcmPlayer } = load('../src/features/agent/audio.ts', {}, { AudioContext })
  const player = new PcmPlayer(() => {})
  await player.unlock()
  player.start()
  player.push(new Uint8Array([255]).buffer)
  player.push(new Uint8Array([127, 0, 128]).buffer)
  assert.equal(sources.length, 1)
  assert.equal(sources[0].buffer.samples[0], 32767 / 32768)
  assert.equal(sources[0].buffer.samples[1], -1)
  player.dispose()
})

test('microphone worklet produces 16kHz little-endian PCM across fractional block boundaries', () => {
  const source = readFileSync(new URL('../public/agent-pcm-worklet.js', import.meta.url), 'utf8')
  for (const sampleRate of [16000, 44100, 48000]) {
    const sent = []
    let Processor
    class Base { port = { postMessage: (message) => sent.push(message) } }
    runInNewContext(source, { AudioWorkletProcessor: Base, sampleRate, registerProcessor: (_, value) => { Processor = value }, Int16Array, ArrayBuffer, DataView, Math })
    const processor = new Processor()
    for (let offset = 0; offset < sampleRate / 10; offset += 128) processor.process([[new Float32Array(Math.min(128, sampleRate / 10 - offset)).fill(0.25)]])
    processor.port.onmessage({ data: { type: 'stop', discard: false } })
    const frames = sent.filter((message) => message.type === 'audio')
    assert.equal(frames.reduce((total, frame) => total + frame.buffer.byteLength, 0), 3200)
    assert.equal(new DataView(frames[0].buffer).getInt16(0, true), 8192)
  }
})
