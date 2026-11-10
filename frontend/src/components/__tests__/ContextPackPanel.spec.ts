import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ContextPackPanel from '@/components/ContextPackPanel.vue'
import type { QueueItem } from '@/types'

const item = (over: Partial<QueueItem> = {}): QueueItem => ({
  session_id: 's1',
  summary: '用户诉求：退款；用户表述：我要投诉；转人工原因：风险拦截',
  priority: 'high',
  priority_reason: '风险触发：命中投诉关键词',
  trigger_type: 'risk',
  trigger_detail: '',
  attempted_actions: [
    { tool: 'order.get', result_status: 'OK', at: '2026-11-10T08:00:00Z' },
    { tool: 'logistics.get', result_status: 'OK', at: '2026-11-10T08:00:01Z' },
  ],
  suggested_next_step: null,
  primary_intent: 'complaint',
  sentiment: 'angry',
  order_id: 'ORD1002',
  queued_at: '2026-11-10T08:00:00Z',
  ...over,
})

describe('ContextPackPanel', () => {
  it('展示摘要 / 已尝试动作 / 优先级依据 / 触发类型', () => {
    const w = mount(ContextPackPanel, { props: { item: item() } })
    expect(w.find('[data-test="summary"]').text()).toContain('用户诉求')
    expect(w.find('[data-test="attempted"]').text()).toContain('已查过：订单、物流')
    expect(w.find('[data-test="priority-reason"]').text()).toContain('风险触发')
    expect(w.find('[data-test="trigger-type"]').text()).toContain('风险拦截')
  })

  it('风险触发时不渲染 AI 建议区块（FR-012）', () => {
    const w = mount(ContextPackPanel, { props: { item: item({ suggested_next_step: null }) } })
    expect(w.find('[data-test="ai-suggestion"]').exists()).toBe(false)
  })

  it('有建议时渲染', () => {
    const w = mount(ContextPackPanel, {
      props: { item: item({ trigger_type: 'user_request', suggested_next_step: '核对订单状态后按退款流程处理' }) },
    })
    expect(w.find('[data-test="ai-suggestion"]').text()).toContain('核对订单状态')
  })

  it('未调工具时显示尚未调用', () => {
    const w = mount(ContextPackPanel, { props: { item: item({ attempted_actions: [] }) } })
    expect(w.find('[data-test="attempted"]').text()).toContain('尚未调用工具')
  })

  it('摘要原样展示，前端不截断改写', () => {
    const long = '甲'.repeat(118) + '…'
    const w = mount(ContextPackPanel, { props: { item: item({ summary: long }) } })
    expect(w.find('[data-test="summary"]').text()).toBe(long)
  })
})
