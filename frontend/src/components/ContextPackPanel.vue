<script setup lang="ts">
import { computed } from 'vue'
import type { QueueItem } from '@/types'

const props = defineProps<{ item: QueueItem }>()

const TOOL_LABEL: Record<string, string> = {
  'order.get': '订单',
  'logistics.get': '物流',
  'refund.create': '退款',
  'address.update': '地址',
  'policy.search': '政策',
}

const TRIGGER_LABEL: Record<string, string> = {
  user_request: '用户主动要求转人工',
  risk: '风险拦截',
  system_error: '系统异常',
  boundary: 'AI 能力范围外',
}

const tried = computed(() => {
  const names = props.item.attempted_actions.map((a) => TOOL_LABEL[a.tool] ?? a.tool)
  return [...new Set(names)]
})

// FR-012：风险触发时后端把 suggested_next_step 置空，前端不判断触发类型，
// 只看有没有值——判定权在后端 context_pack。
const showSuggestion = computed(() => props.item.suggested_next_step !== null)
</script>

<template>
  <section class="pack" data-test="context-pack">
    <h3>上下文包</h3>

    <div class="block">
      <span class="label">摘要</span>
      <p data-test="summary">{{ item.summary }}</p>
    </div>

    <div class="block">
      <span class="label">已尝试动作</span>
      <p v-if="tried.length" data-test="attempted">已查过：{{ tried.join('、') }}</p>
      <p v-else class="muted" data-test="attempted">尚未调用工具</p>
    </div>

    <div class="block">
      <span class="label">优先级依据</span>
      <p data-test="priority-reason">{{ item.priority_reason }}</p>
    </div>

    <div class="block">
      <span class="label">触发类型</span>
      <p data-test="trigger-type">{{ TRIGGER_LABEL[item.trigger_type] ?? item.trigger_type }}</p>
      <p v-if="item.trigger_detail" class="muted small">{{ item.trigger_detail }}</p>
    </div>

    <div v-if="showSuggestion" class="block" data-test="ai-suggestion">
      <span class="label">AI 建议</span>
      <p>{{ item.suggested_next_step }}</p>
    </div>
  </section>
</template>

<style scoped>
.pack {
  padding: 16px;
  border-bottom: 1px solid #ebeef5;
}
h3 {
  margin: 0 0 12px;
  font-size: 14px;
}
.block {
  margin-bottom: 12px;
}
.label {
  display: block;
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
p {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: #303133;
}
.muted {
  color: #909399;
}
.small {
  font-size: 12px;
  margin-top: 2px;
}
</style>
