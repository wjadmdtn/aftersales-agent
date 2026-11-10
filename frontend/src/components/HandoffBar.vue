<script setup lang="ts">
import { computed } from 'vue'
import type { UiState } from '@/types'

const props = defineProps<{
  uiState: UiState
  agentName?: string | null
  canHandoff?: boolean
  handoffSent?: boolean
}>()

defineEmits<{ (e: 'handoff'): void }>()

// 四类状态（handoff_requested 并入 ai_serving，见 spec FR-004）
const label = computed(() => {
  switch (props.uiState) {
    case 'ai_serving':
      return 'AI 助手'
    case 'queued':
      return '正在接入人工客服'
    case 'human_serving':
      return `人工客服 ${props.agentName || ''} 正在为您服务`.replace('  ', ' ')
    case 'resolved':
      return '会话已结束'
  }
})

const tagType = computed(() => {
  switch (props.uiState) {
    case 'ai_serving':
      return 'info'
    case 'queued':
      return 'warning'
    case 'human_serving':
      return 'success'
    case 'resolved':
      return 'info'
  }
})

const inputDisabled = computed(() => props.uiState === 'queued' || props.uiState === 'resolved')

const handoffDisabled = computed(
  () => !props.canHandoff || props.uiState !== 'ai_serving' || props.handoffSent,
)

defineExpose({ label, inputDisabled })
</script>

<template>
  <div class="bar" data-test="handoff-bar">
    <el-tag :type="tagType" data-test="state-label" size="large">{{ label }}</el-tag>
    <el-button
      v-if="uiState === 'ai_serving'"
      data-test="quick-handoff"
      size="small"
      :disabled="handoffDisabled"
      @click="$emit('handoff')"
    >
      {{ handoffSent ? '已提交，等待接入' : '转人工' }}
    </el-button>
  </div>
</template>

<style scoped>
.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-bottom: 1px solid #ebeef5;
  background: #fff;
}
</style>
