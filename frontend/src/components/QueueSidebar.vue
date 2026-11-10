<script setup lang="ts">
import type { QueueItem } from '@/types'

defineProps<{ items: QueueItem[]; activeId?: string | null }>()
defineEmits<{ (e: 'select', sessionId: string): void }>()

const PRIORITY_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低' }
</script>

<template>
  <aside class="sidebar">
    <div class="head">
      排队中
      <span class="count">{{ items.length }}</span>
    </div>
    <p v-if="!items.length" class="empty">暂无排队会话</p>
    <ul>
      <li
        v-for="it in items"
        :key="it.session_id"
        :class="['item', { 'item--high': it.priority === 'high', 'item--active': it.session_id === activeId }]"
        :data-test="`queue-${it.session_id}`"
        @click="$emit('select', it.session_id)"
      >
        <div class="row">
          <span class="sid">{{ it.session_id }}</span>
          <span :class="['prio', `prio--${it.priority}`]" :data-test="`prio-${it.session_id}`">
            {{ PRIORITY_LABEL[it.priority] }}
          </span>
        </div>
        <div class="summary">{{ it.summary }}</div>
        <div class="meta">
          {{ it.trigger_type }} · {{ it.order_id ?? '无订单' }}
        </div>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 300px;
  border-right: 1px solid #ebeef5;
  display: flex;
  flex-direction: column;
  height: 100%;
  box-sizing: border-box;
}
.head {
  padding: 14px 16px;
  font-weight: 600;
  border-bottom: 1px solid #ebeef5;
}
.count {
  color: #909399;
  font-weight: 400;
  margin-left: 6px;
}
.empty {
  padding: 24px 16px;
  color: #909399;
  font-size: 13px;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
}
.item {
  padding: 12px 16px;
  border-bottom: 1px solid #f2f2f2;
  cursor: pointer;
}
.item:hover,
.item--active {
  background: #f5f7fa;
}
.item--high {
  border-left: 3px solid #f56c6c;
}
.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}
.sid {
  font-size: 12px;
  color: #909399;
}
.prio {
  font-size: 12px;
  padding: 0 6px;
  border-radius: 4px;
}
.prio--high {
  background: #fef0f0;
  color: #f56c6c;
}
.prio--medium {
  background: #fdf6ec;
  color: #e6a23c;
}
.prio--low {
  background: #f4f4f5;
  color: #909399;
}
.summary {
  font-size: 13px;
  line-height: 1.5;
  color: #303133;
  margin-bottom: 4px;
}
.meta {
  font-size: 12px;
  color: #909399;
}
</style>
