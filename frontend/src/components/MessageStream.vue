<script setup lang="ts">
import type { ChatMessage, PendingAssistant } from '@/types'

defineProps<{
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
</script>

<template>
  <div class="stream" data-test="message-stream">
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
      <div class="bubble">{{ pending.text }}</div>
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
</style>
