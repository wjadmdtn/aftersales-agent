<script setup lang="ts">
import type { MySession, QueueItem } from '@/types'

defineProps<{
  items: QueueItem[]
  /** 我已接入的会话。刷新后队列是空的，这些仍要能看到并进入。 */
  mine?: MySession[]
  activeId?: string | null
}>()
defineEmits<{ (e: 'select', sessionId: string): void }>()

const PRIORITY_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低' }
const STATE_LABEL: Record<string, string> = {
  human_serving: '服务中',
  handoff_requested: '接入中',
  queued: '接入中',
  resolved: '已结束',
  normal: 'AI 处理',
}
</script>

<template>
  <aside class="sidebar">
    <!-- 我的会话：刷新后队列为空时，这里是唯一的入口 -->
    <section v-if="mine?.length" data-test="my-sessions">
      <div class="head">
        我的会话
        <span class="count">{{ mine.length }}</span>
      </div>
      <ul>
        <li
          v-for="s in mine"
          :key="s.session_id"
          :class="['item', { 'item--active': s.session_id === activeId }]"
          :data-test="`mine-${s.session_id}`"
          @click="$emit('select', s.session_id)"
        >
          <div class="row">
            <span class="sid">{{ s.session_id }}</span>
            <span class="prio" :data-test="`mine-state-${s.session_id}`">
              {{ STATE_LABEL[s.state] ?? s.state }}
            </span>
          </div>
          <div class="summary">{{ s.last_message ?? '（暂无消息）' }}</div>
          <div class="meta">{{ s.order_id ?? '无订单' }}</div>
        </li>
      </ul>
    </section>

    <section class="queue-block">
      <div class="head">
        排队中
        <span class="count">{{ items.length }}</span>
      </div>
      <p v-if="!items.length" class="empty">
        {{ mine?.length ? '暂无新的排队会话' : '暂无排队会话' }}
      </p>
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
    </section>
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
  overflow-y: auto;
}
.queue-block {
  border-top: 6px solid #f5f7fa;
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
