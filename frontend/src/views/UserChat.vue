<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import MessageStream from '@/components/MessageStream.vue'
import HandoffBar from '@/components/HandoffBar.vue'
import { Socket, type ConnState } from '@/ws/socket'
import { mergeMessages } from '@/ws/merge'
import { api } from '@/api/rest'
import type { ChatMessage, Envelope, PendingAssistant, UiState } from '@/types'

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
const handoffSent = ref(false)
const conn = ref<ConnState>('connecting')
const input = ref('')
const notice = ref('')

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
  () => uiState.value === 'queued' || uiState.value === 'resolved',
)

const placeholder = computed(() => {
  if (uiState.value === 'queued') return '正在为您接入，请稍候'
  if (uiState.value === 'resolved') return '会话已结束'
  return '输入消息…'
})

function applyState(p: any) {
  uiState.value = p.ui_state as UiState
  agentName.value = p.agent_name ?? null
  if (p.order_id) orderId.value = p.order_id
}

function handleFrame(env: Envelope<any>) {
  switch (env.type) {
    case 'session.state':
      applyState(env.payload)
      break
    case 'user.message.ack': {
      const localId = env.payload.local_id
      if (localId && localId === lastLocalId) {
        // 乐观上屏的那条换成服务端版本
        messages.value = messages.value.filter((m) => !m.id.startsWith('local:'))
        lastLocalId = null
      }
      const msg = env.payload.message as ChatMessage
      messages.value = mergeMessages(messages.value, [msg]).messages
      break
    }
    case 'assistant.delta': {
      const p = env.payload
      if (!pending.value || pending.value.msg_id !== p.msg_id) {
        pending.value = { msg_id: p.msg_id, seq: env.seq, text: '', streaming: p.streaming }
      }
      pending.value.text += p.text_delta
      break
    }
    case 'assistant.done': {
      const p = env.payload
      pending.value = null
      degraded.value = Boolean(p.degraded)
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
    case 'error': {
      if (env.payload.code === 'rate_limited') notice.value = '发送太快了，请稍后再试'
      else notice.value = env.payload.message ?? '出错了'
      break
    }
    default:
      break
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
  } catch {
    // 会话还不存在，正常情况
  }

  const params = new URLSearchParams({ session_id: sessionId.value, user_id: userId.value })
  if (!messages.value.length && orderId.value) params.set('order_id', orderId.value)

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
  input.value = ''
  degraded.value = false
}

function quickHandoff() {
  if (!socket || handoffSent.value) return
  handoffSent.value = true
  socket.send({ type: 'user.handoff', payload: {} })
}

onMounted(boot)
onBeforeUnmount(() => socket?.close())
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
      :messages="messages"
      :pending="pending"
      :degraded="degraded"
      :interrupted="interrupted"
      @handoff="quickHandoff"
      @retry="backfill"
    />

    <div v-if="notice" class="notice">{{ notice }}</div>

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
footer {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
}
</style>
