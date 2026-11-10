<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import QueueSidebar from '@/components/QueueSidebar.vue'
import ContextPackPanel from '@/components/ContextPackPanel.vue'
import MessageStream from '@/components/MessageStream.vue'
import { Socket } from '@/ws/socket'
import { mergeMessages } from '@/ws/merge'
import { api } from '@/api/rest'
import type { ChatMessage, Envelope, QueueItem, UiState } from '@/types'

const agentId = ref(localStorage.getItem('aftersales.agent_id') ?? 'demo-agent')
const agentName = ref(localStorage.getItem('aftersales.agent_name') ?? '小王')

const queue = ref<QueueItem[]>([])
const mySessions = ref<string[]>([])
const activeId = ref<string | null>(null)
const messagesBySession = ref<Record<string, ChatMessage[]>>({})
const statesBySession = ref<Record<string, { ui_state: UiState; agent_name?: string | null }>>({})
const input = ref('')
const notice = ref('')

let socket: Socket | null = null

const activeItem = computed(() => queue.value.find((i) => i.session_id === activeId.value) ?? null)
const activeMessages = computed(() => messagesBySession.value[activeId.value ?? ''] ?? [])
const activeIsMine = computed(
  () => activeId.value !== null && mySessions.value.includes(activeId.value),
)

async function loadQueue() {
  const r = await api.getQueue()
  // 后端已按优先级 + 时间排好（ZSET score），前端不重排
  queue.value = r.items
}

async function loadMine() {
  const r = await api.listSessions(agentId.value)
  mySessions.value = r.items.map((i) => i.session_id)
}

function handleFrame(env: Envelope<any>) {
  if (env.type === 'queue.update') {
    void loadQueue()
    return
  }
  if (env.type === 'session.state') {
    const sid = env.payload.session_id as string
    statesBySession.value[sid] = {
      ui_state: env.payload.ui_state,
      agent_name: env.payload.agent_name,
    }
    if (env.payload.agent_id === agentId.value && !mySessions.value.includes(sid)) {
      mySessions.value = [...mySessions.value, sid]
    }
    return
  }
  if (env.type === 'human.message') {
    const msg = env.payload as ChatMessage
    const sid = msg.session_id
    messagesBySession.value = {
      ...messagesBySession.value,
      [sid]: mergeMessages(messagesBySession.value[sid] ?? [], [msg]).messages,
    }
  }
}

async function open(sessionId: string) {
  activeId.value = sessionId
  if (messagesBySession.value[sessionId]) return
  try {
    const snap = await api.getSession(sessionId)
    messagesBySession.value = {
      ...messagesBySession.value,
      [sessionId]: mergeMessages([], snap.messages).messages,
    }
    statesBySession.value[sessionId] = {
      ui_state: snap.state.ui_state,
      agent_name: snap.state.agent_name,
    }
  } catch {
    /* 会话可能刚创建 */
  }
}

async function accept() {
  if (!activeId.value) return
  const r = await fetch('/handoff/accept', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ session_id: activeId.value, agent_id: agentId.value }),
  })
  if (r.status === 409) {
    notice.value = '该会话已被其他客服接入'
    return
  }
  if (!r.ok) {
    notice.value = '接入失败，请重试'
    return
  }
  await loadMine()
  await loadQueue()
}

async function decline() {
  if (!activeId.value) return
  await api.decline(activeId.value, agentId.value)
  await loadQueue()
}

async function resolve() {
  if (!activeId.value) return
  await api.resolve(activeId.value, agentId.value)
  await loadQueue()
  await loadMine()
}

function send() {
  const text = input.value.trim()
  if (!text || !activeId.value || !socket) return
  socket.send({ type: 'agent.message', payload: { session_id: activeId.value, text } })
  input.value = ''
  notice.value = ''
}

onMounted(async () => {
  await loadQueue()
  await loadMine()
  socket = new Socket({
    path: `/ws/agent?agent_id=${encodeURIComponent(agentId.value)}`,
    onFrame: handleFrame,
    onReopen: () => {
      void loadQueue()
      void loadMine()
    },
  })
  socket.connect()
})

onBeforeUnmount(() => socket?.close())
</script>

<template>
  <div class="console">
    <QueueSidebar :items="queue" :active-id="activeId" @select="open" />

    <main>
      <p v-if="!activeId" class="placeholder">从左侧选择会话</p>

      <template v-else>
        <div class="panel-head">
          <span class="sid">{{ activeId }}</span>
          <span class="state" data-test="agent-state">
            {{ statesBySession[activeId]?.ui_state ?? 'unknown' }}
          </span>
          <div class="actions">
            <el-button
              v-if="!activeIsMine"
              type="primary"
              size="small"
              data-test="accept"
              @click="accept"
            >
              接入
            </el-button>
            <el-button size="small" data-test="decline" @click="decline">拒绝</el-button>
            <el-button v-if="activeIsMine" size="small" type="danger" data-test="resolve" @click="resolve">
              结束会话
            </el-button>
          </div>
        </div>

        <div v-if="notice" class="notice">{{ notice }}</div>

        <ContextPackPanel v-if="activeItem" :item="activeItem" />

        <MessageStream :messages="activeMessages" />

        <footer v-if="activeIsMine">
          <el-input v-model="input" placeholder="回复用户…" data-test="agent-input" @keyup.enter="send" />
          <el-button type="primary" data-test="agent-send" @click="send">发送</el-button>
        </footer>
      </template>
    </main>
  </div>
</template>

<style scoped>
.console {
  display: flex;
  height: 100%;
}
main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.placeholder {
  margin: auto;
  color: #909399;
}
.panel-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid #ebeef5;
}
.sid {
  font-weight: 600;
}
.state {
  font-size: 12px;
  color: #909399;
}
.actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}
.notice {
  padding: 8px 16px;
  background: #fdf6ec;
  color: #e6a23c;
  font-size: 13px;
}
footer {
  display: flex;
  gap: 8px;
  padding: 12px 16px;
  border-top: 1px solid #ebeef5;
}
</style>
