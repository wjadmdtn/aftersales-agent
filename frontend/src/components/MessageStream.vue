<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { ChatMessage, PendingAssistant } from '@/types'

const props = defineProps<{
  messages: ChatMessage[]
  pending?: PendingAssistant | null
  degraded?: boolean
  interrupted?: boolean
}>()

defineEmits<{ (e: 'handoff'): void; (e: 'retry'): void }>()

const ROLE_LABEL: Record<string, string> = {
  user: '我',
  assistant: 'AI 助手',
  human: '人工客服',
  system: '系统',
}

// ---- 自动滚到底部 ----
// 聊天窗口的心智模型是"最新消息总在底部"。此前完全没有滚动逻辑，
// 用户发完消息还得自己往下拖才能看到回复。
const streamEl = ref<HTMLElement | null>(null)
// 用户主动往上翻看历史时不要强行拽回底部——那会打断阅读。
// 只有原本就贴在底部（或自己发的消息）才自动跟随。
const pinnedToBottom = ref(true)

const NEAR_BOTTOM_PX = 80

function onScroll() {
  const el = streamEl.value
  if (!el) return
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight
  pinnedToBottom.value = distance <= NEAR_BOTTOM_PX
}

function scrollToBottom(smooth = false) {
  const el = streamEl.value
  if (!el) return
  el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
}

async function followIfPinned() {
  if (!pinnedToBottom.value) return
  await nextTick()
  scrollToBottom(true)
}

// 新消息到达（含自己发的）
watch(() => props.messages.length, followIfPinned)
// 流式输出过程中也要跟随，否则长回复会撑出可视区
watch(
  () => props.pending?.text,
  async () => {
    if (!pinnedToBottom.value) return
    await nextTick()
    scrollToBottom()
  },
)
// 出现等待占位（刚发出消息）一定回到底部——这是用户自己的操作
watch(
  () => Boolean(props.pending),
  async (has) => {
    if (!has) return
    pinnedToBottom.value = true
    await nextTick()
    scrollToBottom(true)
  },
)

defineExpose({ scrollToBottom })
</script>

<template>
  <div ref="streamEl" class="stream" data-test="message-stream" @scroll.passive="onScroll">
    <div
      v-for="m in messages"
      :key="m.seq"
      class="msg"
      :class="`msg--${m.role}`"
      :data-test="`msg-${m.role}`"
    >
      <div class="meta">{{ ROLE_LABEL[m.role] ?? m.role }}</div>
      <div class="bubble">{{ m.text }}</div>
    </div>

    <div v-if="pending" class="msg msg--assistant" data-test="msg-pending">
      <div class="meta">AI 助手</div>
      <!-- 首个 delta 到达前 text 为空：显示"正在输入"，否则是空白气泡 -->
      <div v-if="!pending.text" class="bubble bubble--typing" data-test="typing">
        <span class="dot" /><span class="dot" /><span class="dot" />
      </div>
      <div v-else class="bubble">{{ pending.text }}</div>
    </div>

    <div v-if="degraded" class="degrade" data-test="degraded">
      <p>抱歉，系统暂时有点繁忙，没能帮您处理。您可以稍后再试一次，如果着急的话，也可以转人工客服。</p>
      <el-button data-test="handoff-after-degrade" size="small" type="primary" @click="$emit('handoff')">
        转人工客服
      </el-button>
    </div>

    <div v-if="interrupted" class="interrupted" data-test="interrupted">
      <span>连接中断，以上内容已保留。</span>
      <el-button size="small" data-test="retry" @click="$emit('retry')">重试</el-button>
    </div>
  </div>
</template>

<style scoped>
.stream {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  overflow-y: auto;
  flex: 1;
}
.msg {
  display: flex;
  flex-direction: column;
  max-width: 72%;
}
.msg--user {
  align-self: flex-end;
  align-items: flex-end;
}
.msg--assistant,
.msg--system {
  align-self: flex-start;
}
.msg--human {
  align-self: flex-start;
}
.meta {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.bubble {
  padding: 10px 14px;
  border-radius: 8px;
  background: #f4f4f5;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
}
.msg--user .bubble {
  background: #409eff;
  color: #fff;
}
.msg--human .bubble {
  background: #f0f9eb;
  border: 1px solid #e1f3d8;
}
.degrade {
  background: #fdf6ec;
  border: 1px solid #faecd8;
  padding: 12px;
  border-radius: 8px;
}
.degrade p {
  margin: 0 0 8px;
}
.interrupted {
  display: flex;
  gap: 8px;
  align-items: center;
  color: #e6a23c;
  font-size: 13px;
}

/* 正在输入：三点跳动。政策链路首次响应可达 20~40s，必须有可见反馈 */
.bubble--typing {
  display: inline-flex;
  gap: 4px;
  align-items: center;
  min-width: 48px;
}

.bubble--typing .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #909399;
  animation: typing-bounce 1.2s infinite ease-in-out;
}

.bubble--typing .dot:nth-child(2) {
  animation-delay: 0.15s;
}

.bubble--typing .dot:nth-child(3) {
  animation-delay: 0.3s;
}

@keyframes typing-bounce {
  0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}
</style>
