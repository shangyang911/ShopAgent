import { computed, ref } from 'vue'
import {
  AgentApiError,
  agentSocketUrl,
  archiveAgentConversation,
  createAgentConversation,
  fetchAgentConversation,
  fetchAgentStatus,
} from './api'
import { PcmPlayer, PcmRecorder } from './audio'
import type {
  AgentAction,
  AgentActionResult,
  AgentConversation,
  AgentMessage,
  AgentStatus,
  AgentTicket,
} from './types'

export function useAgentChat(username: string) {
  // 每次调用拥有独立会话资源；页面卸载时须 dispose，释放连接、麦克风和定时器。
  const status = ref<AgentStatus | null>(null)
  const conversationId = ref('')
  const messages = ref<AgentMessage[]>([])
  const actions = ref<AgentAction[]>([])
  const tickets = ref<AgentTicket[]>([])
  const connection = ref<'connecting' | 'online' | 'reconnecting' | 'offline' | 'unauthorized'>(
    'connecting',
  )
  const initializing = ref(true)
  const busy = ref(false)
  const remoteBusy = ref(false)
  const phase = ref('idle')
  const error = ref('')
  const notice = ref('')
  const transcript = ref('')
  const recording = ref(false)
  const preparing = ref(false)
  const endingRecording = ref(false)
  const elapsed = ref(0)
  const level = ref(0)
  const playing = ref(false)
  const now = ref(Date.now())
  const speechEnabled = ref(true)
  const recentConversations = ref<{ id: string; title: string }[]>([])
  const removingConversation = ref<string | null>(null)
  const removalError = ref('')
  const storageKey = `corelystics.agent.${username}`
  let socket: WebSocket | null = null
  let disposed = false
  let socketGeneration = 0
  // 连接、历史加载、录音权限请求分别编号，旧异步任务完成后不能改变新会话。
  let loadGeneration = 0
  let captureGeneration = 0
  let activeRequest: string | null = null
  let failedRequest: string | null = null
  let speechRequest: string | null = null
  let frames: ArrayBuffer[] = []
  let recordingStarted = 0
  let recordingReady = false
  let reconnectAttempts = 0
  let heartbeat: ReturnType<typeof setInterval> | undefined
  let clock: ReturnType<typeof setInterval> | undefined
  let recordingTimer: ReturnType<typeof setInterval> | undefined
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined
  let connectionTimer: ReturnType<typeof setTimeout> | undefined
  let ready: {
    promise: Promise<void>
    resolve: () => void
    reject: (reason: Error) => void
    timer: ReturnType<typeof setTimeout>
  } | null = null
  const recorder = new PcmRecorder()
  const player = new PcmPlayer((value) => {
    playing.value = value
  })

  try {
    speechEnabled.value = localStorage.getItem(`${storageKey}.speech`) !== 'false'
    const recent = JSON.parse(localStorage.getItem(`${storageKey}.recent`) ?? '[]')
    if (Array.isArray(recent))
      recentConversations.value = recent
        .filter((item) => typeof item.id === 'string' && typeof item.title === 'string')
        .slice(0, 8)
  } catch {
    /* Storage is optional. */
  }
  const canInteract = computed(
    () =>
      connection.value === 'online' &&
      !initializing.value &&
      !remoteBusy.value &&
      !preparing.value &&
      !recording.value &&
      !endingRecording.value &&
      !removingConversation.value &&
      (!busy.value || phase.value === 'speaking'),
  )
  const canChat = computed(() => canInteract.value && !!status.value?.capabilities.chat)
  const canRecord = computed(() => canInteract.value && !!status.value?.capabilities.voice_input)
  const pendingAction = computed(() =>
    [...actions.value]
      .reverse()
      .find((a) => a.state === 'pending' && Date.parse(a.expires_at) > now.value),
  )
  const processingLabel = computed(
    () =>
      ({
        idle: '',
        recording: '正在聆听',
        thinking: '正在理解你的问题',
        processing: '正在处理',
        search_orders: '正在查询订单',
        get_order: '正在核对订单详情',
        get_logistics: '正在查询物流记录',
        search_products: '正在查询商品库存',
        search_knowledge: '正在查阅知识资料',
        prepare_cancel_order: '正在准备操作确认',
        create_handoff_ticket: '正在创建人工工单',
        committing: '正在提交已确认的操作',
        speaking: '正在语音播报',
      })[phase.value] ?? '正在处理',
  )

  function remember(id: string) {
    const title =
      messages.value.find((message) => message.role === 'user')?.text.slice(0, 24) || '新会话'
    recentConversations.value = [
      { id, title },
      ...recentConversations.value.filter((item) => item.id !== id),
    ].slice(0, 8)
    try {
      localStorage.setItem(storageKey, id)
      localStorage.setItem(`${storageKey}.recent`, JSON.stringify(recentConversations.value))
    } catch {
      /* The conversation still works. */
    }
  }

  function forget(id: string) {
    recentConversations.value = recentConversations.value.filter((item) => item.id !== id)
    try {
      localStorage.setItem(`${storageKey}.recent`, JSON.stringify(recentConversations.value))
      if (localStorage.getItem(storageKey) === id) localStorage.removeItem(storageKey)
    } catch {
      /* Server-side archival still prevents restoring this conversation. */
    }
  }

  function updateAction(action: AgentAction) {
    const index = actions.value.findIndex((item) => item.id === action.id)
    if (index < 0) actions.value.push(action)
    else actions.value[index] = action
  }

  function updateTicket(ticket: AgentTicket) {
    const index = tickets.value.findIndex((item) => item.id === ticket.id)
    if (index < 0) tickets.value.push(ticket)
    else tickets.value[index] = ticket
  }

  function upsertMessage(message: AgentMessage) {
    const index = messages.value.findIndex(
      (item) => item.request_id === message.request_id && item.role === message.role,
    )
    if (index < 0) messages.value.push(message)
    else messages.value[index] = { ...messages.value[index], ...message }
    if (message.action) updateAction(message.action)
    if (message.ticket) updateTicket(message.ticket)
    if (conversationId.value) remember(conversationId.value)
  }

  function applyConversation(data: AgentConversation) {
    const unmatched = messages.value.filter(
      (m) =>
        m.delivery &&
        !data.messages.some((item) => item.request_id === m.request_id && item.role === m.role),
    )
    messages.value = [
      ...data.messages,
      ...unmatched.map((m) => ({ ...m, delivery: 'interrupted' as const })),
    ]
    actions.value = data.actions
    tickets.value = data.tickets
    remoteBusy.value = data.busy
  }

  async function refreshHistory() {
    if (!conversationId.value || disposed) return
    const id = conversationId.value
    try {
      const data = await fetchAgentConversation(id)
      if (!disposed && conversationId.value === id && !busy.value) applyConversation(data)
    } catch (reason) {
      showFailure(reason)
    }
  }

  function showFailure(reason: unknown) {
    if (disposed) return
    error.value = reason instanceof Error ? reason.message : '客服连接失败，请稍后重试。'
    if (reason instanceof AgentApiError && reason.status === 401) connection.value = 'unauthorized'
  }

  function clearTimers() {
    clearInterval(heartbeat)
    clearTimeout(connectionTimer)
    clearTimeout(reconnectTimer)
  }

  function send(event: Record<string, unknown>) {
    if (socket?.readyState !== WebSocket.OPEN) return false
    socket.send(JSON.stringify(event))
    return true
  }

  function stopPlayback() {
    speechRequest = null
    player.stop()
  }

  function rejectReady() {
    if (ready) {
      clearTimeout(ready.timer)
      ready.reject(new Error('录音已结束'))
      ready = null
    }
  }

  function stopCapture() {
    ++captureGeneration
    clearInterval(recordingTimer)
    recording.value = preparing.value = false
    frames = []
    level.value = 0
    rejectReady()
    void recorder.stop(true)
  }

  function clearTurn() {
    activeRequest = null
    busy.value = false
    phase.value = 'idle'
    remoteBusy.value = false
  }

  function onEvent(event: Record<string, unknown>) {
    // request_id 标识一轮问答。打断或新提问后，上一轮迟到的消息不能继续更新当前轮。
    const type = event.type
    if (type === 'session.ready') {
      if (reconnectAttempts > 0) notice.value = '连接已恢复，对话记录已同步。'
      connection.value = 'online'
      initializing.value = false
      reconnectAttempts = 0
      clearTimeout(connectionTimer)
      if (status.value && event.capabilities)
        status.value.capabilities = event.capabilities as AgentStatus['capabilities']
      return
    }
    if (type === 'pong') return
    const id = event.request_id as string | undefined
    if (type === 'turn.done' && id === failedRequest && id !== activeRequest) {
      failedRequest = null
      if (!busy.value) {
        remoteBusy.value = false
        void refreshHistory()
      }
      return
    }
    if (id && id !== activeRequest) return
    if (type === 'audio.ready') {
      recordingReady = true
      if (ready) clearTimeout(ready.timer)
      ready?.resolve()
      return
    }
    if (type === 'transcript') {
      transcript.value = String(event.text ?? '')
      return
    }
    if (type === 'user.message' && id) {
      upsertMessage({
        role: 'user',
        text: String(event.text),
        request_id: id,
        created_at: new Date().toISOString(),
        delivery: 'sent',
        source: event.source === 'voice' ? 'voice' : 'text',
      })
      transcript.value = ''
    } else if (type === 'state' && phase.value !== 'committing') {
      phase.value = String(event.tool ?? event.state)
    } else if (type === 'assistant.message') {
      upsertMessage(event.message as AgentMessage)
    } else if (type === 'action.required') {
      updateAction(event.action as AgentAction)
    } else if (type === 'action.result') {
      const result = event.action_result as AgentActionResult
      const action = actions.value.find((item) => item.id === result.action_id)
      if (action) updateAction({ ...action, state: 'completed', result })
    } else if (type === 'ticket.created') {
      updateTicket(event.ticket as AgentTicket)
    } else if (type === 'audio.start') {
      if (speechEnabled.value && id) {
        phase.value = 'speaking'
        speechRequest = id
        player.start(Number(event.sample_rate) || 24000)
      }
    } else if (type === 'audio.end') {
      speechRequest = null
    } else if (type === 'warning') {
      notice.value = String(event.message ?? '')
    } else if (type === 'error') {
      error.value = String(event.message ?? '本次请求未能完成。')
      if (event.code === 'action_in_progress') return
      const current = activeRequest
      failedRequest = current
      if (recording.value || preparing.value) {
        stopCapture()
        if (event.code === 'audio_backpressure' && current)
          send({ type: 'turn.cancel', request_id: current })
      }
      for (const message of messages.value) {
        if (message.request_id === current && message.delivery === 'sending')
          message.delivery = 'failed'
      }
      stopPlayback()
      clearTurn()
      if (event.code === 'unauthorized') {
        connection.value = 'unauthorized'
        socket?.close()
      } else void refreshHistory()
    } else if (type === 'turn.done') {
      if (event.status !== 'completed') {
        for (const message of messages.value) {
          if (message.request_id === id && message.delivery === 'sending')
            message.delivery = 'interrupted'
        }
      }
      clearTurn()
      void refreshHistory()
    }
  }

  function openSocket() {
    // 更换连接前递增编号；旧连接触发的关闭/消息回调会被编号检查丢弃。
    const generation = ++socketGeneration
    clearTimers()
    socket?.close()
    const next = new WebSocket(agentSocketUrl(conversationId.value))
    socket = next
    next.binaryType = 'arraybuffer'
    connectionTimer = setTimeout(() => {
      if (generation === socketGeneration) next.close()
    }, 12000)
    next.onopen = () => {
      if (disposed || generation !== socketGeneration) return
      heartbeat = setInterval(() => send({ type: 'ping' }), 25000)
    }
    next.onmessage = ({ data }) => {
      if (disposed || generation !== socketGeneration) return
      if (data instanceof ArrayBuffer) {
        if (
          speechRequest &&
          speechRequest === activeRequest &&
          speechEnabled.value &&
          !player.push(data)
        )
          notice.value = '浏览器暂时无法播放声音，请查看文字回复。'
      } else {
        try {
          onEvent(JSON.parse(String(data)))
        } catch {
          error.value = '客服消息读取失败，请刷新会话。'
        }
      }
    }
    next.onclose = () => {
      if (disposed || generation !== socketGeneration) return
      clearTimers()
      stopCapture()
      stopPlayback()
      for (const message of messages.value) {
        if (message.request_id === activeRequest && message.delivery === 'sending')
          message.delivery = 'interrupted'
      }
      clearTurn()
      initializing.value = false
      if (connection.value === 'unauthorized') return
      if (reconnectAttempts >= 5) {
        connection.value = 'offline'
        return
      }
      connection.value = 'reconnecting'
      notice.value = '连接已中断，正在恢复会话。已确认的操作不会自动重复提交。'
      reconnectTimer = setTimeout(
        () => {
          reconnectAttempts++
          void initialize()
        },
        Math.min(1000 * 2 ** reconnectAttempts, 15000),
      )
    }
    next.onerror = () => {
      /* onclose handles reconnection and session recovery. */
    }
  }

  async function initialize(forceNew = false) {
    if (removingConversation.value) return
    const generation = ++loadGeneration
    initializing.value = true
    error.value = ''
    connection.value = reconnectAttempts ? 'reconnecting' : 'connecting'
    try {
      const configured = await fetchAgentStatus()
      if (disposed || generation !== loadGeneration) return
      status.value = configured
      let saved = conversationId.value
      if (!saved && !forceNew) {
        try {
          saved = localStorage.getItem(storageKey) ?? ''
        } catch {
          /* Optional. */
        }
      }
      let data: AgentConversation | null = null
      if (saved && !forceNew) {
        try {
          data = await fetchAgentConversation(saved)
        } catch (reason) {
          if (!(reason instanceof AgentApiError && reason.status === 404)) throw reason
          forget(saved)
          if (conversationId.value === saved) messages.value = []
        }
      }
      if (!data) {
        const created = await createAgentConversation()
        data = { id: created.id, messages: [], actions: [], tickets: [], busy: false }
      }
      if (disposed || generation !== loadGeneration) return
      conversationId.value = data.id
      if (forceNew) messages.value = []
      applyConversation(data)
      remember(data.id)
      openSocket()
      clock ??= setInterval(() => {
        now.value = Date.now()
      }, 1000)
    } catch (reason) {
      if (disposed || generation !== loadGeneration) return
      initializing.value = false
      connection.value = 'offline'
      showFailure(reason)
      if (
        !(reason instanceof AgentApiError && reason.status === 401) &&
        reconnectAttempts > 0 &&
        reconnectAttempts < 5
      ) {
        connection.value = 'reconnecting'
        reconnectTimer = setTimeout(
          () => {
            reconnectAttempts++
            void initialize()
          },
          Math.min(1000 * 2 ** reconnectAttempts, 15000),
        )
      }
    }
  }

  function request(event: Record<string, unknown>, text?: string) {
    if (!canInteract.value) return null
    stopPlayback()
    error.value = notice.value = ''
    const id = crypto.randomUUID()
    activeRequest = id
    busy.value = true
    phase.value = event.type === 'action.confirm' ? 'committing' : 'thinking'
    if (!send({ ...event, request_id: id })) {
      clearTurn()
      error.value = '客服连接已断开，请重新连接。'
      return null
    }
    if (text)
      upsertMessage({
        role: 'user',
        text,
        request_id: id,
        created_at: new Date().toISOString(),
        delivery: 'sending',
      })
    return id
  }

  async function unlockAudio() {
    if (!speechEnabled.value) return
    try {
      await player.unlock()
    } catch {
      notice.value = '声音暂时无法启用，文字聊天仍可使用。'
    }
  }

  function sendText(text: string) {
    const value = text.trim()
    if (!canChat.value || !value || value.length > (status.value?.limits.text_characters ?? 2000))
      return false
    void unlockAudio()
    return !!request({ type: 'chat.send', text: value, speak: speechEnabled.value }, value)
  }

  function sendFrames() {
    if (!recordingReady || !activeRequest) return
    if (socket?.readyState !== WebSocket.OPEN || socket.bufferedAmount > 1024 * 1024)
      throw new Error('语音上传积压，请重新录制或改用文字。')
    for (const frame of frames) socket.send(frame)
    frames = []
  }

  async function beginRecording() {
    if (!canRecord.value) return
    const generation = ++captureGeneration
    preparing.value = true
    recordingReady = false
    frames = []
    error.value = notice.value = transcript.value = ''
    stopPlayback()
    void unlockAudio()
    try {
      const started = await recorder.start((buffer, volume) => {
        if (generation !== captureGeneration || disposed) return
        level.value = Math.min(1, volume * 5)
        frames.push(buffer)
        try {
          sendFrames()
        } catch (reason) {
          showFailure(reason)
          void finishRecording(true)
        }
      })
      if (!started || disposed || generation !== captureGeneration) return
      preparing.value = false
      const id = request({
        type: 'audio.start',
        format: 'pcm_s16le',
        sample_rate: 16000,
        channels: 1,
        speak: speechEnabled.value,
      })
      if (!id) {
        await recorder.stop(true)
        return
      }
      recording.value = true
      phase.value = 'recording'
      elapsed.value = 0
      recordingStarted = Date.now()
      recordingReady = false
      const promise = new Promise<void>((resolve, reject) => {
        ready = {
          promise: null as unknown as Promise<void>,
          resolve,
          reject,
          timer: setTimeout(() => reject(new Error('录音连接超时，请重试。')), 12000),
        }
      })
      ready!.promise = promise
      recordingTimer = setInterval(() => {
        elapsed.value = Math.floor((Date.now() - recordingStarted) / 1000)
        if (elapsed.value >= (status.value?.limits.audio_seconds ?? 60)) {
          notice.value = '录音已达到时长上限，正在发送。'
          void finishRecording()
        }
      }, 200)
      await promise
      if (generation === captureGeneration && !disposed) sendFrames()
    } catch (reason) {
      if (generation !== captureGeneration || disposed) return
      showFailure(reason)
      await finishRecording(true)
    } finally {
      if (generation === captureGeneration) preparing.value = false
    }
  }

  async function finishRecording(cancel = false) {
    if (endingRecording.value) return
    if (!recording.value && !preparing.value) return
    endingRecording.value = true
    clearInterval(recordingTimer)
    const id = activeRequest
    try {
      if (preparing.value || cancel) {
        ++captureGeneration
        preparing.value = recording.value = false
        rejectReady()
        frames = []
        await recorder.stop(true)
        if (id) send({ type: 'turn.cancel', request_id: id })
        transcript.value = ''
      } else {
        recording.value = false
        phase.value = 'thinking'
        try {
          await recorder.stop(false)
          if (ready) await ready.promise
          if (activeRequest !== id || disposed) return
          sendFrames()
          if (Date.now() - recordingStarted < 300) {
            if (id) send({ type: 'turn.cancel', request_id: id })
            notice.value = '说话时间太短，请重新按住说话。'
          } else send({ type: 'audio.commit', request_id: id })
        } catch (reason) {
          showFailure(reason)
          if (id) send({ type: 'turn.cancel', request_id: id })
        }
      }
    } finally {
      if (ready) clearTimeout(ready.timer)
      ready = null
      level.value = 0
      endingRecording.value = false
    }
  }

  function cancelTurn() {
    if (phase.value === 'committing') return
    stopPlayback()
    if (recording.value || preparing.value) {
      void finishRecording(true)
      return
    }
    if (activeRequest) send({ type: 'turn.cancel', request_id: activeRequest })
  }

  function confirmAction(action: AgentAction) {
    if (action.state !== 'pending' || Date.parse(action.expires_at) <= now.value) return
    void unlockAudio()
    request({ type: 'action.confirm', action_id: action.id, speak: speechEnabled.value })
  }

  function declineAction() {
    request({ type: 'action.decline', speak: false })
  }
  function handoff(summary: string) {
    const value = summary.trim()
    if (!value || value.length > 1000) return false
    return !!request({ type: 'handoff.request', summary: value, speak: false })
  }

  function toggleSpeech() {
    speechEnabled.value = !speechEnabled.value
    try {
      localStorage.setItem(`${storageKey}.speech`, String(speechEnabled.value))
    } catch {
      /* Optional. */
    }
    if (speechEnabled.value) void unlockAudio()
    else {
      stopPlayback()
      if (phase.value === 'speaking' && activeRequest)
        send({ type: 'turn.cancel', request_id: activeRequest })
    }
  }

  async function newConversation() {
    if (busy.value || preparing.value || endingRecording.value || removingConversation.value) return
    stopPlayback()
    await initialize(true)
  }

  async function switchConversation(id: string) {
    if (
      busy.value ||
      preparing.value ||
      endingRecording.value ||
      removingConversation.value ||
      id === conversationId.value
    )
      return
    stopPlayback()
    ++socketGeneration
    clearTimers()
    socket?.close()
    conversationId.value = id
    messages.value = []
    await initialize()
  }

  async function removeConversation(id: string) {
    if (
      disposed ||
      initializing.value ||
      busy.value ||
      preparing.value ||
      endingRecording.value ||
      removingConversation.value
    )
      return false
    if (!recentConversations.value.some((item) => item.id === id)) return false
    removingConversation.value = id
    removalError.value = ''
    try {
      try {
        await archiveAgentConversation(id)
      } catch (reason) {
        // 其他标签页已删除、或历史记录已失效时，同样清理本地入口。
        if (!(reason instanceof AgentApiError && reason.status === 404)) throw reason
      }
      if (disposed) return true
      forget(id)
      if (id === conversationId.value) {
        ++loadGeneration
        ++socketGeneration
        clearTimers()
        stopPlayback()
        socket?.close()
        socket = null
        conversationId.value = ''
        messages.value = []
        actions.value = []
        tickets.value = []
        reconnectAttempts = 0
        removingConversation.value = null
        await initialize(true)
      }
      notice.value = '会话已从最近会话中删除。'
      return true
    } catch (reason) {
      if (!disposed) {
        removalError.value = reason instanceof Error ? reason.message : '删除会话失败，请重试。'
        if (reason instanceof AgentApiError && reason.status === 401) showFailure(reason)
      }
      return false
    } finally {
      removingConversation.value = null
      if (!disposed && connection.value === 'reconnecting' && !initializing.value) void initialize()
    }
  }

  function dispose() {
    if (activeRequest && phase.value !== 'committing')
      send({ type: 'turn.cancel', request_id: activeRequest })
    disposed = true
    ++loadGeneration
    ++socketGeneration
    clearTimers()
    clearInterval(clock)
    stopCapture()
    player.dispose()
    socket?.close()
    socket = null
  }

  return {
    status,
    conversationId,
    messages,
    actions,
    tickets,
    connection,
    initializing,
    busy,
    remoteBusy,
    phase,
    error,
    notice,
    transcript,
    recording,
    preparing,
    endingRecording,
    elapsed,
    level,
    playing,
    now,
    speechEnabled,
    canInteract,
    canChat,
    canRecord,
    pendingAction,
    processingLabel,
    recentConversations,
    removingConversation,
    removalError,
    initialize,
    refreshHistory,
    sendText,
    beginRecording,
    finishRecording,
    cancelTurn,
    confirmAction,
    declineAction,
    handoff,
    toggleSpeech,
    stopPlayback,
    newConversation,
    switchConversation,
    removeConversation,
    dispose,
  }
}
