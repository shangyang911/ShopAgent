<script setup lang="ts">
// 此页面负责展示、键盘/手势和弹窗；连接、录音、消息及重连状态由 useAgentChat 管理。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ArrowDown,
  Archive,
  ArrowUpRight,
  AudioLines,
  BotMessageSquare,
  Check,
  CheckCheck,
  ChevronRight,
  ClipboardList,
  CornerDownLeft,
  Headset,
  Keyboard,
  LoaderCircle,
  MessageSquare,
  Mic,
  PackageSearch,
  Plus,
  RefreshCw,
  Send,
  ShieldCheck,
  Square,
  Volume2,
  VolumeX,
  X,
} from '@lucide/vue'
import OperationsShell from '../app/layouts/OperationsShell.vue'
import { money } from '../shared/commerce'
import { fetchAgentTicket } from '../features/agent/api'
import { useAgentChat } from '../features/agent/useAgentChat'
import KnowledgeEvidence from '../features/agent/KnowledgeEvidence.vue'
import AgentFeedback from '../features/agent/AgentFeedback.vue'
import type { AgentAction, AgentMessage, AgentTicket } from '../features/agent/types'

const props = defineProps<{ username: string }>()
const chat = useAgentChat(props.username)
const {
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
  processingLabel,
  recentConversations,
  removingConversation,
  removalError,
} = chat
const draft = ref('')
const voiceMode = ref(false)
const composing = ref(false)
const holding = ref(false)
const slidingCancel = ref(false)
const historyElement = ref<HTMLElement | null>(null)
const textarea = ref<HTMLTextAreaElement | null>(null)
const followBottom = ref(true)
const handoffOpen = ref(false)
const removalTarget = ref<{ id: string; title: string } | null>(null)
const handoffSummary = ref('')
const handoffInput = ref<HTMLTextAreaElement | null>(null)
const modal = ref<HTMLElement | null>(null)
const selectedTicket = ref<AgentTicket | null>(null)
const ticketLoading = ref(false)
const ticketError = ref('')
let pointerId: number | null = null
let pointerStartY = 0
let previousFocus: HTMLElement | null = null
let ticketGeneration = 0
const suggestions = [
  {
    icon: ClipboardList,
    title: '智能补货',
    text: '分析哪些SKU需要补货，覆盖未来7天',
    detail: '库存、需求与草稿计划',
  },
  {
    icon: PackageSearch,
    title: '待发货分析',
    text: '分析当前待发货队列有哪些阻塞条件',
    detail: '运单、售后与面单核对',
  },
  {
    icon: PackageSearch,
    title: '查询订单',
    text: '帮我查询待发货的订单',
    detail: '订单状态与商品明细',
  },
  {
    icon: ClipboardList,
    title: '查商品库存',
    text: '我想查询商品库存，请告诉我需要提供什么信息',
    detail: '商品价格与可用库存',
  },
  {
    icon: MessageSquare,
    title: '咨询售后',
    text: '门店的退货规则是什么？',
    detail: '查阅规则或交由人工核实',
  },
]
const connectionText = computed(
  () =>
    ({
      online: '已连接',
      connecting: '正在连接',
      reconnecting: '正在重连',
      offline: '连接已断开',
      unauthorized: '登录已失效',
    })[connection.value],
)
const showWelcome = computed(
  () => !initializing.value && !messages.value.length && !recording.value && !transcript.value,
)
const inputDisabled = computed(
  () => !canChat.value && !recording.value && phase.value !== 'speaking',
)
const latestUser = computed(() =>
  [...messages.value].reverse().find((message) => message.role === 'user'),
)
const showThinking = computed(
  () => busy.value && !recording.value && !preparing.value && phase.value !== 'speaking',
)
const microphoneLabel = computed(() =>
  preparing.value
    ? '正在开启麦克风…'
    : recording.value
      ? slidingCancel.value
        ? '松开取消'
        : '松开发送，上滑取消'
      : endingRecording.value
        ? '正在发送语音…'
        : '按住说话',
)

function time(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? ''
    : new Intl.DateTimeFormat('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'Asia/Shanghai',
      }).format(date)
}

function actionFor(message: AgentMessage) {
  // 历史消息保存的是动作快照；优先使用最新动作状态，避免已完成操作仍显示“待确认”。
  if (!message.action) return undefined
  return actions.value.find((action) => action.id === message.action?.id) ?? message.action
}

function actionState(action: AgentAction) {
  return action.state === 'pending' && Date.parse(action.expires_at) <= now.value
    ? 'expired'
    : action.state
}

function actionLabel(action: AgentAction) {
  return {
    pending: '等待确认',
    completed: '已完成',
    declined: '已撤销',
    expired: '确认已过期',
    superseded: '已被新请求替代',
    cancelled: '已撤销',
    stale: '订单已变化，请重新查询',
  }[actionState(action)]
}

function ticketLabel(ticket: AgentTicket) {
  return { pending: '待人工处理', in_progress: '处理中', resolved: '已解决' }[ticket.state]
}

function submit(text = draft.value) {
  if (chat.sendText(text)) {
    draft.value = ''
    followBottom.value = true
  }
}

function onEnter(event: KeyboardEvent) {
  if (event.isComposing || composing.value) return
  event.preventDefault()
  submit()
}

function scrollToBottom() {
  void nextTick(() => {
    const element = historyElement.value
    if (element) element.scrollTop = element.scrollHeight
  })
}

function onScroll() {
  const element = historyElement.value
  if (element)
    followBottom.value = element.scrollHeight - element.scrollTop - element.clientHeight < 90
}

function jumpToLatest() {
  followBottom.value = true
  scrollToBottom()
}

async function toggleInputMode() {
  voiceMode.value = !voiceMode.value
  await nextTick()
  textarea.value?.focus()
}

function press(event: PointerEvent) {
  if (event.button !== 0 || !canRecord.value || holding.value) return
  pointerId = event.pointerId
  pointerStartY = event.clientY
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  holding.value = true
  slidingCancel.value = false
  followBottom.value = true
  void chat.beginRecording()
}

function move(event: PointerEvent) {
  if (holding.value && event.pointerId === pointerId)
    slidingCancel.value = pointerStartY - event.clientY > 64
}

function release(event?: PointerEvent, cancel = false) {
  if (!holding.value || (event && event.pointerId !== pointerId)) return
  holding.value = false
  pointerId = null
  void chat.finishRecording(cancel || slidingCancel.value)
  slidingCancel.value = false
}

function keyboardPress(event: KeyboardEvent) {
  if (event.repeat || holding.value || !canRecord.value) return
  holding.value = true
  slidingCancel.value = false
  void chat.beginRecording()
}

function cancelRecording() {
  if (holding.value) release(undefined, true)
  else if (recording.value || preparing.value) void chat.finishRecording(true)
}

function onVisibility() {
  if (document.hidden) {
    cancelRecording()
    chat.stopPlayback()
  }
}

async function openHandoff() {
  previousFocus = document.activeElement as HTMLElement | null
  handoffSummary.value = latestUser.value?.text.slice(0, 1000) ?? ''
  handoffOpen.value = true
  await nextTick()
  handoffInput.value?.focus()
}

function closeModal() {
  if (removingConversation.value) return
  handoffOpen.value = false
  removalTarget.value = null
  selectedTicket.value = null
  ticketError.value = ''
  ++ticketGeneration
  if (previousFocus?.isConnected) previousFocus.focus()
  else textarea.value?.focus()
}

async function openRemoval(recent: { id: string; title: string }) {
  previousFocus = document.activeElement as HTMLElement | null
  removalTarget.value = recent
  removalError.value = ''
  await nextTick()
  modal.value?.querySelector<HTMLButtonElement>('[data-cancel-removal]')?.focus()
}

async function confirmRemoval() {
  if (removalTarget.value && (await chat.removeConversation(removalTarget.value.id))) closeModal()
}

function submitHandoff() {
  if (chat.handoff(handoffSummary.value)) closeModal()
}

async function openTicket(ticket: AgentTicket) {
  previousFocus = document.activeElement as HTMLElement | null
  const generation = ++ticketGeneration
  selectedTicket.value = ticket
  ticketLoading.value = true
  ticketError.value = ''
  await nextTick()
  modal.value?.querySelector<HTMLButtonElement>('button')?.focus()
  try {
    const result = await fetchAgentTicket(ticket.id)
    if (generation === ticketGeneration) selectedTicket.value = result
  } catch (reason) {
    if (generation === ticketGeneration)
      ticketError.value = reason instanceof Error ? reason.message : '工单读取失败'
  } finally {
    if (generation === ticketGeneration) ticketLoading.value = false
  }
}

function trapFocus(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeModal()
    return
  }
  if (event.key !== 'Tab') return
  const elements = modal.value?.querySelectorAll<HTMLElement>(
    'button:not(:disabled), textarea, a[href]',
  )
  if (!elements?.length) return
  const first = elements[0],
    last = elements[elements.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

watch(
  () => [messages.value.length, transcript.value, phase.value, error.value],
  () => {
    if (followBottom.value) scrollToBottom()
  },
)
watch(conversationId, () => {
  followBottom.value = true
  scrollToBottom()
})
watch([recording, preparing], () => {
  if (!recording.value && !preparing.value) holding.value = false
})
onMounted(() => {
  void chat.initialize()
  window.addEventListener('blur', cancelRecording)
  document.addEventListener('visibilitychange', onVisibility)
})
onBeforeUnmount(() => {
  ++ticketGeneration
  window.removeEventListener('blur', cancelRecording)
  document.removeEventListener('visibilitychange', onVisibility)
  chat.dispose()
})
</script>

<template>
  <OperationsShell
    active-page="agent"
    title="店内运营助手"
    :username="username"
    class="agent-shell"
  >
    <div class="agent-page-heading">
      <a class="ops-button" href="/knowledge">门店知识库</a>
      <div>
        <p class="agent-eyebrow">你的业务协作助手</p>
        <h1>店内运营助手<span>查订单、查库存、跟进物流</span></h1>
      </div>
      <button
        class="agent-plain-button"
        :disabled="busy || initializing || preparing || endingRecording || !!removingConversation"
        @click="chat.newConversation"
      >
        <Plus :size="16" />新会话
      </button>
    </div>

    <div class="agent-layout">
      <section class="agent-chat" aria-label="运营助手对话">
        <header class="agent-chat-header">
          <div class="agent-identity">
            <span class="agent-avatar"><BotMessageSquare :size="22" /></span>
            <div><strong>Corelystics 助手</strong><span>查询业务 · 解答问题 · 跟进处理</span></div>
          </div>
          <div class="agent-header-actions">
            <span class="agent-connection" :class="connection"><i />{{ connectionText }}</span>
            <button
              class="agent-icon-button"
              :aria-label="speechEnabled ? '关闭语音播报' : '开启语音播报'"
              :title="speechEnabled ? '关闭语音播报' : '开启语音播报'"
              :aria-pressed="speechEnabled"
              @click="chat.toggleSpeech"
            >
              <Volume2 v-if="speechEnabled" :size="18" /><VolumeX v-else :size="18" />
            </button>
            <button class="agent-help-button" :disabled="!canInteract" @click="openHandoff">
              <Headset :size="16" /><span>人工协助</span>
            </button>
          </div>
        </header>

        <div v-if="connection === 'unauthorized'" class="agent-banner error" role="alert">
          登录已失效，请重新登录。<a href="/login">前往登录<ArrowUpRight :size="14" /></a>
        </div>
        <div
          v-else-if="connection === 'offline' || connection === 'reconnecting'"
          class="agent-banner"
          role="status"
        >
          <span>{{
            connection === 'reconnecting'
              ? '正在恢复连接，原有对话会保留。'
              : '连接已断开，请重新连接后继续。'
          }}</span
          ><button :disabled="initializing" @click="chat.initialize()">
            <RefreshCw :size="14" />重新连接
          </button>
        </div>
        <div v-if="remoteBusy" class="agent-banner" role="status">
          上一条请求仍在处理中，请先核实会话结果。<button @click="chat.refreshHistory">
            <RefreshCw :size="14" />刷新会话
          </button>
        </div>

        <div
          ref="historyElement"
          class="agent-message-list"
          role="log"
          aria-label="客服消息记录"
          aria-live="polite"
          aria-relevant="additions"
          @scroll="onScroll"
        >
          <div v-if="initializing && !messages.length" class="agent-loading" role="status">
            <LoaderCircle class="agent-spin" :size="24" />
            <p>正在连接你的业务助手…</p>
          </div>
          <div v-else-if="showWelcome" class="agent-welcome">
            <span class="agent-welcome-mark"
              ><BotMessageSquare :size="34" :stroke-width="1.5"
            /></span>
            <p class="agent-welcome-kicker">有问题，随时找我</p>
            <h2>你好，{{ username }}<br />今天有什么可以帮你？</h2>
            <p>查询订单、了解库存，或把需要跟进的事情交给我。</p>
            <div class="agent-suggestions">
              <button
                v-for="suggestion in suggestions"
                :key="suggestion.title"
                :disabled="!canChat"
                @click="submit(suggestion.text)"
              >
                <component :is="suggestion.icon" :size="20" /><strong>{{ suggestion.title }}</strong
                ><span>{{ suggestion.detail }}</span
                ><ArrowUpRight :size="15" />
              </button>
            </div>
            <span class="agent-welcome-note"
              ><ShieldCheck :size="14" />涉及订单变更时，会先请你确认</span
            >
          </div>

          <article
            v-for="message in messages"
            :key="`${message.request_id}-${message.role}`"
            class="agent-message"
            :class="message.role"
          >
            <span v-if="message.role === 'assistant'" class="agent-small-avatar"
              ><BotMessageSquare :size="18"
            /></span>
            <div class="agent-message-main">
              <div class="agent-message-meta">
                <strong>{{ message.role === 'assistant' ? 'Corelystics 助手' : '你' }}</strong
                ><span>{{ time(message.created_at) }}</span
                ><Mic v-if="message.source === 'voice'" :size="12" />
              </div>
              <div class="agent-message-bubble">
                <p>{{ message.text }}</p>
              </div>
              <span v-if="message.delivery === 'sending'" class="agent-delivery">发送中…</span>
              <span
                v-if="message.delivery === 'failed' || message.delivery === 'interrupted'"
                class="agent-delivery failed"
                >{{
                  message.delivery === 'failed' ? '本次发送失败' : '连接中断，请先核实处理结果'
                }}</span
              >
              <KnowledgeEvidence v-if="message.sources?.length" :sources="message.sources" />
              <AgentFeedback
                v-if="message.role === 'assistant' && message.trace_id"
                :trace-id="message.trace_id"
              />
              <a
                v-if="message.copilot?.href.startsWith('/inventory?tab=copilot')"
                class="ops-button"
                :href="message.copilot.href"
                >{{ message.copilot.label }}</a
              >

              <section
                v-if="actionFor(message)"
                class="agent-action-card"
                :class="actionState(actionFor(message)!)"
                :aria-label="`订单操作确认 ${actionFor(message)!.order_number}`"
              >
                <div class="agent-action-title">
                  <span><ClipboardList :size="17" />订单操作确认</span
                  ><span class="agent-state-pill">{{ actionLabel(actionFor(message)!) }}</span>
                </div>
                <dl>
                  <div>
                    <dt>操作</dt>
                    <dd>取消订单</dd>
                  </div>
                  <div>
                    <dt>订单号</dt>
                    <dd>{{ actionFor(message)!.order_number }}</dd>
                  </div>
                  <div>
                    <dt>订单金额</dt>
                    <dd>{{ money(actionFor(message)!.total) }}</dd>
                  </div>
                  <div>
                    <dt>取消原因</dt>
                    <dd>{{ actionFor(message)!.reason }}</dd>
                  </div>
                </dl>
                <p class="agent-action-effect">{{ actionFor(message)!.effect }}</p>
                <div
                  v-if="actionState(actionFor(message)!) === 'pending'"
                  class="agent-action-buttons"
                >
                  <button :disabled="!canInteract" @click="chat.declineAction">暂不办理</button
                  ><button
                    class="agent-primary"
                    :disabled="!canInteract"
                    @click="chat.confirmAction(actionFor(message)!)"
                  >
                    <Check :size="15" />确认取消订单
                  </button>
                </div>
                <p
                  v-else-if="actionState(actionFor(message)!) === 'completed'"
                  class="agent-action-success"
                >
                  <CheckCheck :size="15" />订单已取消，处理结果已留存
                </p>
              </section>

              <button
                v-if="message.ticket"
                class="agent-ticket-receipt"
                @click="
                  openTicket(
                    tickets.find((ticket) => ticket.id === message.ticket!.id) ?? message.ticket!,
                  )
                "
              >
                <Headset :size="18" /><span
                  ><strong>人工工单已创建</strong
                  ><small
                    >{{
                      ticketLabel(
                        tickets.find((ticket) => ticket.id === message.ticket!.id) ??
                          message.ticket!,
                      )
                    }}
                    · 查看工单</small
                  ></span
                ><ChevronRight :size="16" />
              </button>
              <a
                v-if="message.action_result"
                class="agent-order-link"
                :href="`/orders?keyword=${encodeURIComponent(message.action_result.order_number)}`"
                >查看订单处理记录<ArrowUpRight :size="14"
              /></a>
            </div>
          </article>

          <div v-if="recording || preparing || transcript" class="agent-live-transcript">
            <Mic :size="16" /><span>{{
              transcript || (preparing ? '正在开启麦克风，请保持按住…' : '正在聆听，请继续说…')
            }}</span>
          </div>
          <div v-if="showThinking" class="agent-thinking" role="status">
            <span class="agent-small-avatar"><BotMessageSquare :size="18" /></span
            ><LoaderCircle class="agent-spin" :size="15" /><span>{{ processingLabel }}</span>
          </div>
        </div>

        <div v-if="!followBottom && messages.length" class="agent-jump">
          <button @click="jumpToLatest"><ArrowDown :size="14" />回到最新消息</button>
        </div>
        <div class="agent-composer">
          <div v-if="error" class="agent-feedback error" role="alert">
            <span>{{ error }}</span
            ><button aria-label="关闭错误提示" @click="error = ''"><X :size="14" /></button>
          </div>
          <div v-else-if="notice" class="agent-feedback" role="status">
            <span>{{ notice }}</span
            ><button aria-label="关闭提示" @click="notice = ''"><X :size="14" /></button>
          </div>
          <div v-if="playing || (busy && phase === 'speaking')" class="agent-playback">
            <AudioLines :size="15" /><span>正在播报回复摘要</span
            ><button @click="chat.cancelTurn"><Square :size="11" />停止播报</button>
          </div>

          <div
            v-if="voiceMode"
            class="agent-voice-input"
            :class="{ recording, cancelling: slidingCancel }"
          >
            <div v-if="recording" class="agent-recording-status">
              <span>{{ elapsed }}s / {{ status?.limits.audio_seconds ?? 60 }}s</span>
              <div class="agent-wave">
                <i
                  v-for="n in 11"
                  :key="n"
                  :style="{ height: `${8 + level * 30 * (0.45 + (n % 4) * 0.18)}px` }"
                />
              </div>
              <span>{{ slidingCancel ? '松开即取消' : '上滑取消' }}</span>
            </div>
            <button
              class="agent-hold-button"
              :class="{ active: holding }"
              :disabled="!canRecord && !recording && !preparing"
              aria-label="按住说话，松开发送，上滑取消"
              :aria-pressed="holding"
              @pointerdown.prevent="press"
              @pointermove="move"
              @pointerup="release($event)"
              @pointercancel="release($event, true)"
              @lostpointercapture="release($event, true)"
              @contextmenu.prevent
              @keydown.space.prevent="keyboardPress"
              @keyup.space.prevent="release()"
              @keydown.esc.prevent="release(undefined, true)"
            >
              <Mic v-if="!preparing" :size="19" /><LoaderCircle
                v-else
                class="agent-spin"
                :size="18"
              />{{ microphoneLabel }}
            </button>
          </div>
          <form v-else class="agent-text-input" @submit.prevent="submit()">
            <textarea
              ref="textarea"
              v-model="draft"
              rows="2"
              :maxlength="status?.limits.text_characters ?? 2000"
              :disabled="inputDisabled"
              aria-label="输入你的问题"
              placeholder="输入你的问题，或切换语音与我交流…"
              @keydown.enter.exact="onEnter"
              @compositionstart="composing = true"
              @compositionend="composing = false"
            />
            <div class="agent-compose-bottom">
              <span><CornerDownLeft :size="12" />Enter 发送 · Shift + Enter 换行</span
              ><span>{{ draft.length }}/{{ status?.limits.text_characters ?? 2000 }}</span>
            </div>
          </form>
          <div class="agent-composer-actions">
            <button
              class="agent-mode-button"
              :disabled="recording || preparing || endingRecording"
              @click="toggleInputMode"
            >
              <Keyboard v-if="voiceMode" :size="17" /><Mic v-else :size="17" />{{
                voiceMode ? '文字输入' : '按住说话'
              }}</button
            ><span class="agent-input-note">重要操作由你确认</span
            ><button
              v-if="busy && phase !== 'speaking' && !recording && !preparing"
              class="agent-stop-button"
              :disabled="phase === 'committing'"
              @click="chat.cancelTurn"
            >
              <Square :size="12" />{{ phase === 'committing' ? '正在办理' : '停止回答' }}</button
            ><button
              v-else-if="!voiceMode"
              class="agent-send-button"
              :disabled="!canChat || !draft.trim()"
              @click="submit()"
            >
              <Send :size="16" />发送
            </button>
          </div>
        </div>
      </section>

      <aside class="agent-context" aria-label="客服工作区">
        <div class="agent-context-intro">
          <span class="agent-context-icon"><Headset :size="21" /></span>
          <h2>让每个问题都有着落</h2>
          <p>从查询到处理，在一段对话里完成。</p>
        </div>
        <div class="agent-context-section">
          <h3>可以帮你做什么</h3>
          <ul class="agent-capabilities">
            <li>
              <PackageSearch :size="17" />
              <div><strong>订单与商品查询</strong><span>查看订单状态、商品价格与库存</span></div>
            </li>
            <li>
              <ShieldCheck :size="17" />
              <div><strong>操作前先确认</strong><span>核对订单与原因后，再执行取消</span></div>
            </li>
            <li>
              <Headset :size="17" />
              <div><strong>人工继续跟进</strong><span>提交工单，保留对话与处理记录</span></div>
            </li>
          </ul>
        </div>
        <div v-if="status && !status.capabilities.knowledge_base" class="agent-knowledge-note">
          <ClipboardList :size="16" />
          <p><strong>门店知识资料待接入</strong><span>退换货等政策暂由人工核实。</span></p>
        </div>
        <div v-if="recentConversations.length" class="agent-context-section agent-recents">
          <h3>最近会话</h3>
          <div
            v-for="recent in recentConversations"
            :key="recent.id"
            class="agent-recent-row"
            :class="{ current: recent.id === conversationId }"
          >
            <button
              class="agent-recent-open"
              :disabled="
                busy || initializing || preparing || endingRecording || !!removingConversation
              "
              :aria-current="recent.id === conversationId ? 'true' : undefined"
              @click="chat.switchConversation(recent.id)"
            >
              <MessageSquare :size="14" /><span>{{ recent.title }}</span>
            </button>
            <button
              type="button"
              class="agent-recent-remove"
              title="删除会话"
              :aria-label="`删除会话：${recent.title}`"
              :disabled="
                busy || initializing || preparing || endingRecording || !!removingConversation
              "
              @click.stop="openRemoval(recent)"
            >
              <LoaderCircle
                v-if="removingConversation === recent.id"
                :size="15"
                class="agent-spin"
              /><Archive v-else :size="15" />
            </button>
          </div>
        </div>
        <div v-if="tickets.length" class="agent-context-section">
          <h3>
            本次会话的人工工单<span>{{ tickets.length }}</span>
          </h3>
          <button
            v-for="ticket in [...tickets].reverse()"
            :key="ticket.id"
            class="agent-context-ticket"
            @click="openTicket(ticket)"
          >
            <span>{{ ticket.summary }}</span
            ><small>{{ ticketLabel(ticket) }}<ChevronRight :size="12" /></small>
          </button>
        </div>
        <div class="agent-context-footer">
          <span><ShieldCheck :size="14" />当前以 {{ username }} 的身份访问</span>
          <div>
            <a href="/orders">订单工作台<ArrowUpRight :size="12" /></a
            ><a href="/inventory">库存中心<ArrowUpRight :size="12" /></a>
          </div>
        </div>
      </aside>
    </div>

    <Teleport to="body"
      ><div
        v-if="handoffOpen || selectedTicket || removalTarget"
        class="agent-modal-overlay"
        @click.self="closeModal"
      >
        <section
          ref="modal"
          class="agent-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="agent-dialog-title"
          :aria-busy="!!removingConversation"
          @keydown="trapFocus"
        >
          <header>
            <span class="agent-avatar"
              ><Archive v-if="removalTarget" :size="21" /><Headset v-else :size="21"
            /></span>
            <div>
              <h2 id="agent-dialog-title">
                {{ removalTarget ? '删除会话' : handoffOpen ? '请人工继续协助' : '人工工单详情' }}
              </h2>
              <p>{{ removalTarget ? '从最近会话中移除' : '对话记录会随工单一起保留' }}</p>
            </div>
            <button
              class="agent-icon-button"
              aria-label="关闭弹窗"
              :disabled="!!removingConversation"
              @click="closeModal"
            >
              <X :size="19" />
            </button>
          </header>
          <form v-if="handoffOpen" @submit.prevent="submitHandoff">
            <label for="agent-handoff-summary">需要协助的问题</label
            ><textarea
              id="agent-handoff-summary"
              ref="handoffInput"
              v-model="handoffSummary"
              rows="5"
              maxlength="1000"
              required
              placeholder="请描述需要人工跟进的事情…"
            />
            <p class="agent-modal-note">提交后将创建待处理工单，当前暂不支持实时人工通话。</p>
            <footer>
              <button class="agent-plain-button" type="button" @click="closeModal">返回对话</button
              ><button
                class="agent-primary"
                :disabled="!handoffSummary.trim() || !canInteract"
                type="submit"
              >
                <Send :size="15" />提交人工工单
              </button>
            </footer>
          </form>
          <div v-else-if="removalTarget" class="agent-removal-confirmation">
            <p>确定删除会话「{{ removalTarget.title }}」吗？</p>
            <p class="agent-modal-note">
              会话将从最近列表中移除。已有人工工单和订单处理记录会保留。
            </p>
            <p v-if="removalError" class="agent-feedback error" role="alert">{{ removalError }}</p>
            <footer>
              <button
                type="button"
                class="agent-plain-button"
                data-cancel-removal
                :disabled="!!removingConversation"
                @click="closeModal"
              >
                取消
              </button>
              <button
                type="button"
                class="agent-primary agent-remove-confirm"
                :disabled="!!removingConversation"
                @click="confirmRemoval"
              >
                <LoaderCircle v-if="removingConversation" :size="15" class="agent-spin" /><Archive
                  v-else
                  :size="15"
                />{{ removingConversation ? '正在删除…' : '确认删除' }}
              </button>
            </footer>
          </div>
          <div v-else-if="selectedTicket" class="agent-ticket-detail">
            <div class="agent-ticket-detail-meta">
              <span class="agent-state-pill">{{ ticketLabel(selectedTicket) }}</span
              ><time>{{ time(selectedTicket.created_at) }}</time>
            </div>
            <p class="agent-ticket-number">工单编号 {{ selectedTicket.id }}</p>
            <h3>问题摘要</h3>
            <p>{{ selectedTicket.summary }}</p>
            <p v-if="ticketLoading" role="status">正在读取对话记录…</p>
            <p v-if="ticketError" class="agent-feedback error" role="alert">{{ ticketError }}</p>
            <div v-if="selectedTicket.transcript?.length" class="agent-ticket-transcript">
              <h3>随工单保存的对话</h3>
              <div v-for="(message, index) in selectedTicket.transcript" :key="index">
                <strong>{{ message.role === 'user' ? '你' : '运营助手' }}</strong>
                <p>{{ message.text }}</p>
              </div>
            </div>
          </div>
        </section>
      </div></Teleport
    >
  </OperationsShell>
</template>

<style scoped>
.agent-shell {
  --agent-accent: #119e91;
  --agent-ink: #233b3d;
  --agent-muted: #7c8d8e;
}
.agent-shell :deep(.operations-content) {
  padding-top: 26px;
}
.agent-page-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 24px;
}
.agent-eyebrow {
  margin: 0 0 7px;
  color: var(--agent-accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1.2px;
}
.agent-page-heading h1 {
  margin: 0;
  color: var(--agent-ink);
  font-size: 25px;
  line-height: 1.3;
  letter-spacing: -0.7px;
}
.agent-page-heading h1 span {
  display: inline-block;
  margin-left: 15px;
  padding-left: 15px;
  border-left: 1px solid #dce7e4;
  color: #8b9998;
  font-size: 12px;
  font-weight: 400;
  letter-spacing: 0;
  vertical-align: middle;
}
.agent-plain-button,
.agent-help-button,
.agent-mode-button,
.agent-stop-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 9px 13px;
  border: 1px solid #e0e9e6;
  border-radius: 9px;
  background: white;
  color: #526865;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}
.agent-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 258px;
  align-items: stretch;
  min-height: 640px;
  height: calc(100dvh - 230px);
  max-height: 1040px;
  gap: 24px;
}
.agent-chat {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border: 1px solid #e1ebe7;
  border-radius: 16px;
  background: #fff;
}
.agent-chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
  gap: 12px;
  padding: 18px 22px;
  border-bottom: 1px solid #edf2ef;
}
.agent-identity {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 11px;
}
.agent-avatar,
.agent-small-avatar {
  display: inline-grid;
  place-items: center;
  flex-shrink: 0;
  width: 42px;
  height: 42px;
  border-radius: 13px;
  color: var(--agent-accent);
  background: #eaf7f3;
}
.agent-identity strong {
  display: block;
  margin-bottom: 5px;
  font-size: 13px;
  color: var(--agent-ink);
}
.agent-identity div > span {
  display: block;
  color: #91a09b;
  font-size: 10px;
}
.agent-header-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.agent-connection {
  display: flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
  color: #8b9996;
  font-size: 10px;
}
.agent-connection i {
  width: 6px;
  height: 6px;
  border-radius: 100%;
  background: #a4b0ac;
}
.agent-connection.online i {
  background: #16a391;
}
.agent-connection.online {
  color: #559489;
}
.agent-icon-button {
  display: inline-grid;
  place-items: center;
  width: 31px;
  height: 31px;
  flex-shrink: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: #7c9089;
  cursor: pointer;
}
.agent-icon-button:hover {
  background: #eff7f3;
  color: #119e91;
}
.agent-help-button {
  padding: 8px 10px;
  font-size: 11px;
}
.agent-message-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 27px 28px;
  scrollbar-width: thin;
  scrollbar-color: #d8e6df transparent;
}
.agent-welcome {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  padding: 24px 0;
  text-align: center;
}
.agent-welcome-mark {
  display: grid;
  place-items: center;
  width: 76px;
  height: 76px;
  margin-bottom: 22px;
  border: 1px solid #d5eee3;
  border-radius: 24px;
  background: #eff9f4;
  color: #159e88;
}
.agent-welcome-kicker {
  margin: 0 0 10px !important;
  color: #78a093 !important;
  font-size: 11px !important;
  letter-spacing: 1.6px;
}
.agent-welcome h2 {
  margin: 0 0 14px;
  color: #29463d;
  font-size: clamp(23px, 2.3vw, 32px);
  line-height: 1.55;
  font-weight: 600;
  letter-spacing: -0.8px;
}
.agent-welcome > p {
  margin: 0;
  color: #8b9a94;
  font-size: 12px;
  line-height: 1.8;
}
.agent-suggestions {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
  width: min(100%, 590px);
  margin: 34px 0 22px;
}
.agent-suggestions button {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
  padding: 18px 14px;
  border: 1px solid #e4ece6;
  border-radius: 12px;
  background: #fafcf9;
  color: #729181;
  text-align: left;
  cursor: pointer;
}
.agent-suggestions button:hover:not(:disabled) {
  border-color: #9dd4c0;
  background: #f3faf5;
}
.agent-suggestions strong {
  color: #405b4c;
  font-size: 12px;
  font-weight: 600;
}
.agent-suggestions span {
  color: #91a090;
  font-size: 10px;
  line-height: 1.5;
}
.agent-suggestions button > svg:last-child {
  position: absolute;
  top: 19px;
  right: 13px;
  color: #9cab9f;
}
.agent-welcome-note {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: #9aa79d;
  font-size: 10px;
}
.agent-loading {
  display: grid;
  place-content: center;
  justify-items: center;
  min-height: 100%;
  gap: 10px;
  color: #7d9990;
  font-size: 12px;
}
.agent-message {
  display: flex;
  gap: 10px;
  margin-bottom: 25px;
}
.agent-small-avatar {
  width: 31px;
  height: 31px;
  margin-top: 21px;
  border-radius: 10px;
}
.agent-message-main {
  max-width: min(88%, 780px);
  min-width: 0;
}
.agent-message-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  color: #a1aca7;
  font-size: 10px;
}
.agent-message-meta strong {
  color: #7f9388;
  font-weight: 400;
}
.agent-message-bubble {
  padding: 14px 17px;
  border: 1px solid #e9efea;
  border-radius: 0 12px 12px;
  background: #f7faf7;
  color: #3d554a;
  font-size: 13px;
  line-height: 1.9;
  overflow-wrap: anywhere;
}
.agent-message-bubble p {
  margin: 0;
  white-space: pre-wrap;
}
.agent-message.user {
  justify-content: flex-end;
}
.agent-message.user .agent-message-meta {
  justify-content: flex-end;
}
.agent-message.user .agent-message-bubble {
  border-color: #d6eee6;
  border-radius: 12px 0 12px 12px;
  background: #e8f6ef;
  color: #28684f;
}
.agent-delivery {
  display: block;
  margin-top: 5px;
  text-align: right;
  color: #95a59c;
  font-size: 10px;
}
.agent-delivery.failed {
  color: #ac6b49;
}
.agent-sources {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
  color: #93a294;
  font-size: 10px;
}
.agent-sources > span:not(:first-child) {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  background: #f1f6ef;
  border-radius: 4px;
  color: #6e8c72;
}
.agent-action-card {
  margin-top: 12px;
  min-width: min(360px, 100%);
  padding: 17px;
  border: 1px solid #d7e9df;
  border-radius: 11px;
  background: #fff;
}
.agent-action-title,
.agent-action-title > span:first-child {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #386d52;
  font-size: 12px;
  font-weight: 600;
}
.agent-state-pill {
  display: inline-flex;
  align-items: center;
  padding: 4px 7px;
  border-radius: 5px;
  background: #f4f6ec;
  color: #87924f;
  font-size: 10px;
  font-weight: 400;
  white-space: nowrap;
}
.agent-action-card.completed .agent-state-pill {
  color: #258757;
  background: #edf8ef;
}
.agent-action-card dl {
  display: grid;
  gap: 10px;
  margin: 19px 0 13px;
  font-size: 11px;
}
.agent-action-card dl div {
  display: grid;
  grid-template-columns: 70px minmax(0, 1fr);
  gap: 10px;
}
.agent-action-card dt {
  color: #97a294;
}
.agent-action-card dd {
  margin: 0;
  color: #4d6250;
  overflow-wrap: anywhere;
}
.agent-action-effect {
  padding: 10px;
  border-radius: 6px;
  background: #faf8f0;
  color: #998456;
  font-size: 10px;
  line-height: 1.8;
}
.agent-action-buttons {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
.agent-action-buttons > button:not(.agent-primary) {
  border: 1px solid #e0e9df;
  border-radius: 7px;
  padding: 8px 12px;
  background: #fff;
  color: #81937f;
  font-size: 11px;
  cursor: pointer;
}
.agent-primary {
  display: inline-flex;
  justify-content: center;
  align-items: center;
  gap: 6px;
  border: 0;
  border-radius: 8px;
  padding: 9px 13px;
  background: #159d86;
  color: white;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}
.agent-action-success {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 0;
  color: #319064;
  font-size: 11px;
}
.agent-ticket-receipt {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  margin-top: 10px;
  padding: 13px;
  border: 1px solid #e1e9df;
  border-radius: 8px;
  background: white;
  color: #6c957b;
  text-align: left;
  cursor: pointer;
}
.agent-ticket-receipt span {
  flex: 1;
}
.agent-ticket-receipt strong,
.agent-ticket-receipt small {
  display: block;
  font-size: 11px;
}
.agent-ticket-receipt small {
  margin-top: 5px;
  color: #9aab9c;
  font-size: 10px;
}
.agent-order-link {
  display: inline-flex;
  gap: 5px;
  margin-top: 9px;
  color: #299574;
  text-decoration: none;
  font-size: 11px;
}
.agent-live-transcript {
  display: flex;
  gap: 10px;
  align-items: center;
  margin: 15px 0 0 40px;
  padding: 15px;
  border: 1px dashed #b7d9c9;
  border-radius: 9px;
  background: #f5faf5;
  color: #5e9076;
  font-size: 12px;
  line-height: 1.7;
}
.agent-thinking {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #839986;
  font-size: 11px;
}
.agent-thinking .agent-small-avatar {
  margin: 0 2px 0 0;
}
.agent-jump {
  position: absolute;
  right: 24px;
  bottom: 205px;
}
.agent-jump button {
  display: flex;
  gap: 5px;
  padding: 7px 11px;
  border: 1px solid #d9e8de;
  border-radius: 20px;
  background: white;
  color: #7a9c83;
  font-size: 10px;
  cursor: pointer;
}
.agent-composer {
  flex-shrink: 0;
  padding: 14px 22px 16px;
  border-top: 1px solid #edf2eb;
  background: #fff;
}
.agent-text-input {
  padding: 13px 14px 9px;
  border: 1px solid #dce8df;
  border-radius: 11px;
  background: #fcfdfb;
}
.agent-text-input:focus-within {
  border-color: #8fcbb3;
}
.agent-text-input textarea {
  display: block;
  width: 100%;
  min-height: 50px;
  max-height: 140px;
  resize: none;
  border: 0;
  outline: 0;
  background: transparent;
  color: #395641;
  font: inherit;
  font-size: 12px;
  line-height: 1.8;
}
.agent-text-input textarea::placeholder {
  color: #a9b5aa;
}
.agent-compose-bottom {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #b1bbae;
  font-size: 9px;
}
.agent-compose-bottom > span:first-child {
  display: flex;
  gap: 5px;
  align-items: center;
}
.agent-composer-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 11px;
}
.agent-mode-button {
  padding: 7px 9px;
  border: 0;
  background: #f2f7f0;
  color: #759578;
  font-size: 10px;
}
.agent-input-note {
  flex: 1;
  color: #a4b09f;
  font-size: 9px;
}
.agent-send-button {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: 8px;
  padding: 9px 17px;
  background: #159d86;
  color: white;
  font-size: 11px;
  cursor: pointer;
}
.agent-send-button:disabled {
  background: #e9f1e6;
  color: #9aad92;
  opacity: 1;
}
.agent-stop-button {
  padding: 8px 11px;
  font-size: 10px;
}
.agent-voice-input {
  padding: 10px;
  border: 1px solid #dce8df;
  border-radius: 11px;
  background: #f8fbf5;
}
.agent-hold-button {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 75px;
  border: 0;
  border-radius: 8px;
  background: #eef6ea;
  color: #578362;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
  -webkit-touch-callout: none;
}
.agent-hold-button.active {
  color: #fff;
  background: #159d86;
}
.agent-voice-input.cancelling .agent-hold-button {
  background: #b76c54;
}
.agent-recording-status {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin: 0 4px 8px;
  color: #7e9a7c;
  font-size: 10px;
}
.agent-wave {
  display: flex;
  align-items: center;
  gap: 3px;
  height: 38px;
}
.agent-wave i {
  display: block;
  width: 3px;
  border-radius: 3px;
  background: #4daf8d;
  transition: height 80ms;
}
.agent-playback {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 11px;
  color: #72a17b;
  font-size: 10px;
}
.agent-playback button {
  display: flex;
  gap: 4px;
  align-items: center;
  margin-left: auto;
  border: 0;
  background: none;
  color: #7f967d;
  font-size: 10px;
  cursor: pointer;
}
.agent-banner,
.agent-feedback {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 18px;
  background: #faf8ef;
  color: #9a895d;
  font-size: 11px;
  line-height: 1.7;
}
.agent-banner button,
.agent-banner a,
.agent-feedback button {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex-shrink: 0;
  border: 0;
  background: none;
  color: inherit;
  font-size: 10px;
  cursor: pointer;
}
.agent-feedback {
  padding: 8px 10px;
  margin-bottom: 10px;
  border-radius: 7px;
}
.error {
  background: #fbf1ec;
  color: #ae755e;
}
.agent-context {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 7px 0 0;
  scrollbar-width: thin;
  color: var(--agent-ink);
}
.agent-context-intro {
  padding-bottom: 24px;
  border-bottom: 1px solid #e9eee5;
}
.agent-context-icon {
  display: grid;
  place-items: center;
  width: 43px;
  height: 43px;
  margin-bottom: 15px;
  border: 1px solid #dcead8;
  border-radius: 12px;
  background: #f5f8ee;
  color: #829c6d;
}
.agent-context-intro h2 {
  margin: 0 0 8px;
  color: #53664a;
  font-size: 15px;
  font-weight: 600;
  letter-spacing: -0.2px;
}
.agent-context-intro p {
  margin: 0;
  color: #a0ab94;
  font-size: 11px;
  line-height: 1.8;
}
.agent-context-section {
  padding: 22px 0;
  border-bottom: 1px solid #e9eee5;
}
.agent-context-section h3 {
  display: flex;
  justify-content: space-between;
  margin: 0 0 18px;
  color: #829276;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.5px;
}
.agent-context-section h3 span {
  color: #a1af94;
}
.agent-capabilities {
  display: grid;
  gap: 21px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.agent-capabilities li {
  display: flex;
  gap: 12px;
  color: #97ae8b;
}
.agent-capabilities li > svg {
  flex-shrink: 0;
  margin-top: 2px;
}
.agent-capabilities strong {
  display: block;
  margin-bottom: 6px;
  color: #6f8560;
  font-size: 11px;
  font-weight: 500;
}
.agent-capabilities span {
  display: block;
  color: #a3af98;
  font-size: 10px;
  line-height: 1.7;
}
.agent-knowledge-note {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin-top: 21px;
  padding: 13px;
  border: 1px solid #eaeadd;
  border-radius: 9px;
  background: #fafaf2;
  color: #a4aa81;
}
.agent-knowledge-note > svg {
  flex-shrink: 0;
}
.agent-knowledge-note p {
  margin: 0;
}
.agent-knowledge-note strong {
  display: block;
  margin: 1px 0 6px;
  font-size: 10px;
  font-weight: 500;
}
.agent-knowledge-note span {
  display: block;
  color: #b0b39c;
  font-size: 10px;
  line-height: 1.7;
}
.agent-recents {
  padding-bottom: 14px;
}
.agent-recents h3 {
  margin-bottom: 10px;
}
.agent-recent-row {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-bottom: 3px;
  padding-right: 4px;
  border-radius: 7px;
}
.agent-recent-row.current {
  background: #f1f6ec;
  color: #6b8d5c;
}
.agent-recent-row:hover {
  background: #f5f8f1;
}
.agent-recent-row button {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 9px 8px;
  border: 0;
  border-radius: 7px;
  background: none;
  color: #8c9a83;
  font-size: 10px;
  text-align: left;
  cursor: pointer;
}
.agent-recent-open {
  flex: 1;
  min-width: 0;
}
.agent-recent-row.current .agent-recent-open {
  color: #6b8d5c;
}
.agent-recent-row .agent-recent-remove {
  justify-content: center;
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  padding: 5px;
  color: #93a58a;
}
.agent-recent-row .agent-recent-remove:hover:not(:disabled) {
  background: #e6efdf;
  color: #507341;
}
.agent-removal-confirmation > p:first-child {
  font-size: 13px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}
.agent-remove-confirm {
  background: #a66d54;
}
.agent-recents button span {
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.agent-recents button svg {
  flex-shrink: 0;
}
.agent-context-ticket {
  display: block;
  width: 100%;
  margin: 0 0 8px;
  padding: 10px;
  border: 1px solid #e4ebdd;
  border-radius: 7px;
  background: white;
  color: #819672;
  text-align: left;
  cursor: pointer;
}
.agent-context-ticket span {
  display: -webkit-box;
  overflow: hidden;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  font-size: 10px;
  line-height: 1.8;
}
.agent-context-ticket small {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  color: #a8b399;
  font-size: 9px;
}
.agent-context-footer {
  margin-top: auto;
  padding-top: 23px;
  color: #aab69f;
  font-size: 9px;
}
.agent-context-footer > span,
.agent-context-footer > div {
  display: flex;
  align-items: center;
  gap: 5px;
}
.agent-context-footer > div {
  gap: 15px;
  margin-top: 14px;
}
.agent-context-footer a {
  display: flex;
  gap: 4px;
  color: #8ba779;
  text-decoration: none;
}
.agent-spin {
  animation: agent-spin 1.2s linear infinite;
}
@keyframes agent-spin {
  to {
    transform: rotate(360deg);
  }
}
.agent-modal-overlay {
  position: fixed;
  z-index: 100;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 20px;
  background: #193a354d;
}
.agent-modal {
  width: min(510px, 100%);
  max-height: 86dvh;
  overflow-y: auto;
  padding: 25px;
  border: 1px solid #e0ebe4;
  border-radius: 17px;
  background: white;
  color: #465d4c;
}
.agent-modal > header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 27px;
}
.agent-modal header > div {
  flex: 1;
}
.agent-modal h2 {
  margin: 0 0 7px;
  color: #3a5745;
  font-size: 17px;
}
.agent-modal header p {
  margin: 0;
  color: #a1af9c;
  font-size: 11px;
}
.agent-modal label,
.agent-modal h3 {
  display: block;
  margin: 0 0 10px;
  color: #798e70;
  font-size: 12px;
  font-weight: 500;
}
.agent-modal textarea {
  box-sizing: border-box;
  width: 100%;
  resize: vertical;
  padding: 12px;
  border: 1px solid #dce7d6;
  border-radius: 9px;
  font: inherit;
  font-size: 12px;
  line-height: 1.8;
  color: #5e7553;
  background: #fcfdf9;
}
.agent-modal-note {
  margin: 12px 0 22px;
  color: #a3af94;
  font-size: 11px;
  line-height: 1.8;
}
.agent-modal footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.agent-ticket-detail-meta {
  display: flex;
  justify-content: space-between;
  color: #a0af94;
  font-size: 10px;
}
.agent-ticket-number {
  margin: 16px 0 22px;
  color: #9aa98f;
  font-size: 10px;
  overflow-wrap: anywhere;
}
.agent-ticket-detail > p {
  line-height: 1.9;
  font-size: 12px;
}
.agent-ticket-transcript {
  margin-top: 22px;
  padding-top: 20px;
  border-top: 1px solid #e7ede0;
}
.agent-ticket-transcript > div {
  margin-bottom: 15px;
}
.agent-ticket-transcript strong {
  color: #8a9c7c;
  font-size: 10px;
  font-weight: 500;
}
.agent-ticket-transcript p {
  margin: 6px 0 0;
  color: #708565;
  font-size: 11px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
@media (max-width: 1220px) {
  .agent-layout {
    grid-template-columns: minmax(0, 1fr) 220px;
    gap: 18px;
  }
  .agent-chat-header {
    padding: 16px;
  }
  .agent-header-actions {
    gap: 6px;
  }
  .agent-message-list {
    padding: 24px 18px;
  }
  .agent-composer {
    padding: 12px 16px 15px;
  }
  .agent-page-heading h1 span {
    display: none;
  }
}
@media (max-width: 1000px) {
  .agent-layout {
    grid-template-columns: minmax(0, 1fr);
  }
  .agent-context {
    display: none;
  }
  .agent-layout {
    max-height: none;
  }
}
@media (max-width: 620px) {
  .agent-shell :deep(.operations-content) {
    padding: 18px 0 0;
  }
  .agent-page-heading {
    gap: 8px;
    margin-bottom: 16px;
  }
  .agent-page-heading h1 {
    font-size: 20px;
  }
  .agent-eyebrow {
    font-size: 9px;
  }
  .agent-plain-button {
    padding: 8px 9px;
    font-size: 10px;
  }
  .agent-layout {
    min-height: 520px;
    height: calc(100dvh - 175px);
  }
  .agent-chat {
    border-radius: 12px;
  }
  .agent-chat-header {
    padding: 13px 10px;
    align-items: flex-start;
  }
  .agent-avatar {
    width: 34px;
    height: 34px;
    border-radius: 10px;
  }
  .agent-identity {
    gap: 7px;
  }
  .agent-identity strong {
    font-size: 11px;
  }
  .agent-identity div > span {
    display: none;
  }
  .agent-header-actions {
    gap: 3px;
  }
  .agent-connection {
    font-size: 9px;
  }
  .agent-help-button {
    padding: 6px;
    border: 0;
  }
  .agent-help-button span {
    display: none;
  }
  .agent-icon-button {
    width: 27px;
    height: 27px;
  }
  .agent-message-list {
    padding: 19px 11px;
  }
  .agent-welcome {
    padding: 15px 0;
  }
  .agent-welcome h2 {
    font-size: 22px;
  }
  .agent-welcome > p {
    max-width: 210px;
    font-size: 11px;
  }
  .agent-welcome-mark {
    width: 60px;
    height: 60px;
    border-radius: 19px;
    margin-bottom: 18px;
  }
  .agent-suggestions {
    grid-template-columns: 1fr;
    margin: 22px 0 18px;
    gap: 7px;
  }
  .agent-suggestions button {
    display: grid;
    grid-template-columns: 20px 1fr;
    gap: 5px 10px;
    padding: 11px;
  }
  .agent-suggestions button > svg:first-child {
    grid-row: span 2;
    align-self: center;
  }
  .agent-suggestions span {
    grid-column: 2;
  }
  .agent-suggestions button > svg:last-child {
    top: 17px;
    right: 12px;
  }
  .agent-suggestions strong {
    font-size: 11px;
  }
  .agent-welcome-note {
    font-size: 9px;
  }
  .agent-message {
    gap: 6px;
  }
  .agent-small-avatar {
    width: 25px;
    height: 25px;
  }
  .agent-message-main {
    max-width: calc(100% - 31px);
  }
  .agent-message-bubble {
    font-size: 12px;
    padding: 11px 12px;
  }
  .agent-action-card {
    padding: 11px;
  }
  .agent-action-title {
    gap: 5px;
  }
  .agent-action-title > span:first-child {
    font-size: 11px;
  }
  .agent-action-card dl div {
    grid-template-columns: 52px minmax(0, 1fr);
    gap: 5px;
  }
  .agent-action-buttons {
    flex-wrap: wrap;
  }
  .agent-composer {
    padding: 10px;
  }
  .agent-text-input {
    padding: 10px;
  }
  .agent-text-input textarea {
    font-size: 12px;
  }
  .agent-compose-bottom > span:first-child {
    font-size: 8px;
  }
  .agent-compose-bottom > span:last-child {
    font-size: 8px;
  }
  .agent-input-note {
    display: none;
  }
  .agent-composer-actions {
    justify-content: space-between;
  }
  .agent-live-transcript {
    margin-left: 0;
    font-size: 11px;
  }
  .agent-modal-overlay {
    padding: 12px;
  }
  .agent-modal {
    padding: 18px;
  }
  .agent-jump {
    right: 15px;
  }
  .agent-banner {
    flex-wrap: wrap;
    font-size: 10px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .agent-spin {
    animation: none;
  }
  .agent-wave i {
    transition: none;
  }
}
</style>
