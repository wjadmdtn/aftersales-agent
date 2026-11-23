<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import MessageStream from '@/components/MessageStream.vue'
import HandoffBar from '@/components/HandoffBar.vue'
import { Socket, type ConnState } from '@/ws/socket'
import { mergeMessages } from '@/ws/merge'
import { api } from '@/api/rest'
import type { ChatMessage, ConfirmCard, Envelope, PendingAssistant, UiState } from '@/types'

const route = useRoute()

const sessionId = ref(localStorage.getItem('aftersales.session_id') ?? '')
const userId = ref(localStorage.getItem('aftersales.user_id') ?? 'u-demo')
const orderId = ref<string | null>(null)

const messages = ref<ChatMessage[]>([])
const pending = ref<PendingAssistant | null>(null)
const uiState = ref<UiState>('ai_serving')
const agentName = ref<string | null>(null)
const degraded = ref(false)
const interrupted = ref(false)
// 空闲超时：5 分钟没有问答，本次会话结束（与后端 chat_inactivity_timeout_s 一致）。
// 前端负责即时反馈，后端在下次发消息时做权威判定。
const idleExpired = ref(false)
const stream = ref<{ scrollToBottom: (smooth?: boolean) => void } | null>(null)
const IDLE_TIMEOUT_MS = 5 * 60 * 1000
let idleTimer: ReturnType<typeof setTimeout> | undefined

function resetIdleTimer() {
  clearTimeout(idleTimer)
  if (idleExpired.value) return
  idleTimer = setTimeout(() => {
    idleExpired.value = true
    pending.value = null
  }, IDLE_TIMEOUT_MS)
}
const handoffSent = ref(false)
const conn = ref<ConnState>('connecting')
const input = ref('')
const notice = ref('')
// 待确认卡：只能通过卡片上的按钮确认/取消，不解析自由文本
const confirmCard = ref<ConfirmCard | null>(null)
const confirming = ref(false)
// 这些终态意味着确认卡已了结，可以移除了
const CONFIRM_ENDED = new Set([
  'executed',
  'cancelled',
  'write_rejected',
  'execute_failed',
  'execute_uncertain',
  'expired',
  'no_pending',
  'stale_card',
])

let socket: Socket | null = null
let lastLocalId: string | null = null

function ensureIds() {
  if (!sessionId.value) {
    sessionId.value = crypto.randomUUID()
    localStorage.setItem('aftersales.session_id', sessionId.value)
  }
  if (!localStorage.getItem('aftersales.user_id')) {
    localStorage.setItem('aftersales.user_id', userId.value)
  }
}

const inputDisabled = computed(
  () =>
    uiState.value === 'queued' ||
    uiState.value === 'resolved' ||
    // AI 正在回复（含等待模型返回）：这一条没回完，不允许再发
    pending.value !== null,
)

// 后端在「要改地址」时回 need_address，这里弹出结构化表单。
// 地址不回填自由文本解析——文本里抽省市区一定出错，抽错就是把货寄错地方。
const agentState = ref<string | null>(null)
const showAddressForm = computed(
  () =>
    agentState.value === 'need_address' &&
    uiState.value === 'ai_serving' &&
    !idleExpired.value,
)
const addressForm = ref({
  receiver: '',
  phone: '',
  province: '',
  city: '',
  district: '',
  detail: '',
})
const addressError = ref('')

function submitAddress() {
  const a = addressForm.value
  const fields = [a.receiver, a.phone, a.province, a.city, a.district, a.detail]
  if (fields.some((x) => !x.trim())) {
    addressError.value = '请填写完整的收货信息'
    return
  }
  if (!/^\d{6,20}$/.test(a.phone.trim())) {
    addressError.value = '手机号格式不正确'
    return
  }
  if (!socket) return
  addressError.value = ''
  socket.send({
    type: 'user.address',
    payload: {
      address: {
        receiver: a.receiver.trim(),
        phone: a.phone.trim(),
        province: a.province.trim(),
        city: a.city.trim(),
        district: a.district.trim(),
        detail: a.detail.trim(),
      },
    },
  })
  // 收起表单，等后端回确认卡；地址写错了重新说"改地址"还能再弹一次
  agentState.value = null
}

const placeholder = computed(() => {
  if (idleExpired.value) return '会话已结束，发送消息可开始新会话'
  if (uiState.value === 'queued') return '正在为您接入，请稍候'
  if (uiState.value === 'resolved') return '会话已结束'
  if (pending.value) return 'AI 正在回复，请稍候…'
  return '输入消息…'
})

// 超时结束提示：历史保留，但明确告诉用户这次结束了
const idleNotice = computed(() =>
  idleExpired.value ? '本次会话已结束（超过 5 分钟未回复）。发送消息可重新开始。' : '',
)

// 人工服务期间绝不能出现 AI 的"正在输入"——那是 AI 在思考的暗示，
// 而此时是人在处理。用户实测反馈：人工接入中发消息，界面一直显示 AI loading。
const aiServing = computed(() => uiState.value === 'ai_serving')

function applyState(p: any) {
  uiState.value = p.ui_state as UiState
  agentName.value = p.agent_name ?? null
  if (p.order_id) orderId.value = p.order_id
  // 状态切走 AI 服务时，残留的 AI 等待占位必须清掉：
  // 已转人工/排队，再显示"AI 正在输入"会误导，且没人会推 delta 来结束它
  if (uiState.value !== 'ai_serving') pending.value = null
}

function handleFrame(env: Envelope<any>) {
  switch (env.type) {
    case 'session.state':
      applyState(env.payload)
      if (uiState.value !== 'ai_serving') {
        confirmCard.value = null
        confirming.value = false
      }
      break
    case 'user.message.ack': {
      const localId = env.payload.local_id as string | undefined
      const msg = env.payload.message as ChatMessage
      // 用服务端版本替换乐观上屏那条：按 local id 精确剔除，避免留下重复消息
      const cleaned = localId
        ? messages.value.filter((m) => m.id !== localId)
        : messages.value
      messages.value = mergeMessages(cleaned, [msg]).messages
      if (localId === lastLocalId) lastLocalId = null
      break
    }
    case 'assistant.delta': {
      const p = env.payload
      // pending 可能是发送时建的本地占位（msg_id 还是 local-xxx），
      // 也可能是上一条 delta 建的：两种情况都按 msg_id 重新对齐
      if (!pending.value || pending.value.msg_id !== p.msg_id) {
        pending.value = { msg_id: p.msg_id, seq: env.seq, text: '', streaming: p.streaming }
      }
      pending.value.text += p.text_delta
      break
    }
    case 'assistant.done': {
      const p = env.payload
      pending.value = null
      resetIdleTimer()
      degraded.value = Boolean(p.degraded)
      // 后端阶段名（need_address 等）驱动表单展示
      agentState.value = p.state ?? null
      confirming.value = false
      if (CONFIRM_ENDED.has(String(p.state ?? ''))) confirmCard.value = null
      const msg: ChatMessage = {
        id: p.msg_id,
        session_id: env.session_id ?? sessionId.value,
        seq: p.seq ?? env.seq,
        role: 'assistant',
        sender_id: null,
        text: p.text,
        streaming: p.streaming ?? false,
        final: true,
        ts: env.ts,
        trace_id: p.trace_id ?? null,
      }
      messages.value = mergeMessages(messages.value, [msg]).messages
      break
    }
    case 'human.message': {
      const msg = env.payload as ChatMessage
      messages.value = mergeMessages(messages.value, [msg]).messages
      break
    }
    case 'confirm.card': {
      confirmCard.value = env.payload as unknown as ConfirmCard
      confirming.value = false
      break
    }
    case 'error': {
      // 出错时清掉等待占位，否则界面会永远停在"正在输入"
      pending.value = null
      // 后端会带上具体等待秒数或字数上限，直接展示比自己编文案更准
      if (env.payload.code === 'rate_limited' || env.payload.code === 'message_too_long') {
        notice.value = env.payload.message ?? '发送太快了，请稍后再试'
      } else notice.value = env.payload.message ?? '出错了'
      break
    }
    default:
      break
  }
}

/** 每次进入对话页，AI 先打招呼（2026-11-12 口径）。 */
function greetingMessage(): ChatMessage {
  return {
    id: `greeting-${crypto.randomUUID()}`,
    session_id: sessionId.value,
    seq: 0,
    role: 'assistant',
    sender_id: null,
    text: '您好，我是售后客服助手，有什么可以帮您？',
    streaming: false,
    final: true,
    ts: new Date().toISOString(),
    trace_id: null,
  }
}

async function backfill() {
  const maxSeq = messages.value.length ? messages.value[messages.value.length - 1].seq : 0
  const snap = await api.getSession(sessionId.value, maxSeq)
  applyState(snap.state)
  messages.value = mergeMessages(messages.value, snap.messages).messages
  if (snap.truncated) notice.value = '部分早期消息已省略'
}

async function boot() {
  ensureIds()
  const queryOrder = String(route.query.order_id ?? '') || null
  orderId.value = queryOrder

  try {
    const snap = await api.getSession(sessionId.value, 0)
    applyState(snap.state)
    messages.value = mergeMessages([], snap.messages).messages
    // 超时的会话：历史照常显示，但本次是全新会话，下面会插入 AI 问候
    if (snap.expired) messages.value = []
  } catch {
    // 会话还不存在，正常情况
  }

  // 每次进入，若本次会话还没有任何消息，AI 先打招呼。
  // 超时重开也走这里——用户看到的是一致的"开场"。
  // 是否新会话要在插入问候语之前判定：问候语会让 messages 非空，
  // 之后再判长度会把 order_id 永远挡在握手之外。
  const isNewSession = !messages.value.length
  if (isNewSession) messages.value = [greetingMessage()]
  resetIdleTimer()

  const params = new URLSearchParams({ session_id: sessionId.value, user_id: userId.value })
  if (isNewSession && orderId.value) params.set('order_id', orderId.value)

  socket = new Socket({
    path: `/ws/chat?${params.toString()}`,
    onFrame: handleFrame,
    onStateChange: (s) => {
      conn.value = s
      interrupted.value = s === 'reconnecting'
    },
    onReopen: () => {
      interrupted.value = false
      void backfill()
    },
  })
  socket.connect()
}

function send() {
  const text = input.value.trim()
  if (!text || !socket) return
  if (idleExpired.value) {
    // 已超时：本次会话结束，按新会话继续（后端同样会重置状态与人工归属）
    idleExpired.value = false
    uiState.value = 'ai_serving'
    agentName.value = null
  }
  resetIdleTimer()
  if (uiState.value === 'queued') {
    notice.value = '正在为您接入，请稍候'
    return
  }
  lastLocalId = `local-${crypto.randomUUID()}`
  messages.value = [
    ...messages.value,
    {
      id: lastLocalId,
      session_id: sessionId.value,
      seq: Number.MAX_SAFE_INTEGER,
      role: 'user',
      sender_id: userId.value,
      text,
      streaming: false,
      final: true,
      ts: new Date().toISOString(),
      trace_id: null,
    },
  ]
  socket.send({ type: 'user.message', payload: { text, local_id: lastLocalId } })
  // 立刻显示等待态：首个 assistant.delta 要等模型返回（政策链路 20~40s），
  // 这段空白期界面毫无反馈，用户会以为没发出去。用本地 id 占位，
  // 首个 delta 到达时按 msg_id 替换成真实消息。
  // 人工服务期间不显示：那是人在回复，显示"AI 正在输入"是错的，
  // 且人工若一直不回，这个占位会永远转下去。
  pending.value = aiServing.value
    ? { msg_id: lastLocalId, seq: 0, text: '', streaming: true }
    : null
  input.value = ''
  degraded.value = false
  // 用户自己发消息：无论之前翻到哪儿，都回到底部
  void nextTick(() => stream.value?.scrollToBottom(true))
}

function quickHandoff() {
  if (!socket || handoffSent.value) return
  handoffSent.value = true
  socket.send({ type: 'user.handoff', payload: {} })
}

function decide(decision: 'confirm' | 'cancel') {
  if (!socket || !confirmCard.value || confirming.value) return
  confirming.value = true
  socket.send({
    type: 'user.confirm',
    payload: { card_id: confirmCard.value.card_id, decision },
  })
}

onMounted(boot)
onBeforeUnmount(() => {
  clearTimeout(idleTimer)
  socket?.close()
})
</script>

<template>
  <div class="chat-page">
    <header>
      <div class="title">
        客服会话
        <span v-if="orderId" class="order" data-test="bound-order">正在咨询：{{ orderId }}</span>
      </div>
    </header>

    <HandoffBar
      :ui-state="uiState"
      :agent-name="agentName"
      :can-handoff="!handoffSent"
      :handoff-sent="handoffSent"
      @handoff="quickHandoff"
    />

    <div v-if="conn !== 'open'" class="banner" data-test="connection-banner">
      {{ conn === 'reconnecting' ? '连接中断，正在重连…' : '连接中…' }}
    </div>

    <MessageStream
      ref="stream"
      :messages="messages"
      :pending="pending"
      :degraded="degraded"
      :interrupted="interrupted"
      @handoff="quickHandoff"
      @retry="backfill"
    />

    <div v-if="idleNotice" class="notice notice--idle" data-test="idle-ended">{{ idleNotice }}</div>
    <div v-if="notice" class="notice">{{ notice }}</div>

    <div v-if="confirmCard" class="confirm-card" data-test="confirm-card">
      <p class="confirm-card__summary">{{ confirmCard.summary }}</p>
      <div class="confirm-card__actions">
        <el-button
          type="primary"
          size="small"
          :loading="confirming"
          :disabled="confirming"
          data-test="confirm-ok"
          @click="decide('confirm')"
        >
          确认
        </el-button>
        <el-button size="small" :disabled="confirming" data-test="confirm-cancel" @click="decide('cancel')">
          取消
        </el-button>
      </div>
    </div>

    <form
      v-if="showAddressForm"
      class="address-form"
      data-test="address-form"
      @submit.prevent="submitAddress"
    >
      <div class="address-form__title">请填写新的收货信息</div>
      <div class="address-form__row">
        <el-input v-model="addressForm.receiver" placeholder="收件人" data-test="addr-receiver" />
        <el-input v-model="addressForm.phone" placeholder="手机号" data-test="addr-phone" />
      </div>
      <div class="address-form__row">
        <el-input v-model="addressForm.province" placeholder="省" data-test="addr-province" />
        <el-input v-model="addressForm.city" placeholder="市" data-test="addr-city" />
        <el-input v-model="addressForm.district" placeholder="区/县" data-test="addr-district" />
      </div>
      <el-input
        v-model="addressForm.detail"
        placeholder="详细地址（街道、门牌号）"
        data-test="addr-detail"
      />
      <div v-if="addressError" class="address-form__error" data-test="addr-error">
        {{ addressError }}
      </div>
      <el-button type="primary" native-type="submit" data-test="addr-submit">
        提交地址
      </el-button>
    </form>

    <footer>
      <el-input
        v-model="input"
        :disabled="inputDisabled"
        :placeholder="placeholder"
        data-test="chat-input"
        @keyup.enter="send"
      />
      <el-button type="primary" :disabled="inputDisabled" data-test="chat-send" @click="send">
        发送
      </el-button>
    </footer>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  max-width: 900px;
  margin: 0 auto;
  border-left: 1px solid #ebeef5;
  border-right: 1px solid #ebeef5;
  box-sizing: border-box;
}
header {
  padding: 14px 16px;
  border-bottom: 1px solid #ebeef5;
}
.title {
  font-weight: 600;
}
.order {
  margin-left: 12px;
  font-weight: 400;
  font-size: 13px;
  color: #409eff;
}
.banner {
  background: #fdf6ec;
  color: #e6a23c;
  font-size: 13px;
  padding: 6px 16px;
}
.notice {
  padding: 6px 16px;
  font-size: 13px;
  color: #909399;
}
.confirm-card {
  margin: 0 16px 12px;
  padding: 12px 16px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  background: #fafafa;
}
.confirm-card__summary {
  margin: 0 0 10px;
  font-size: 14px;
}
.confirm-card__actions {
  display: flex;
  gap: 8px;
}
footer {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
}
.address-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
  background: #fafafa;
}
.address-form__title {
  font-size: 13px;
  color: #606266;
}
.address-form__row {
  display: flex;
  gap: 8px;
}
.address-form__error {
  font-size: 12px;
  color: #f56c6c;
}
.address-form :deep(.el-button) {
  align-self: flex-start;
}
</style>
