import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import QueueSidebar from '@/components/QueueSidebar.vue'
import type { QueueItem } from '@/types'

const item = (over: Partial<QueueItem>): QueueItem => ({
  session_id: 's1',
  summary: '用户诉求：退款',
  priority: 'medium',
  priority_reason: '用户主动要求转人工',
  trigger_type: 'user_request',
  trigger_detail: '',
  attempted_actions: [],
  suggested_next_step: null,
  primary_intent: 'refund',
  sentiment: 'neutral',
  order_id: 'ORD1002',
  queued_at: '2026-11-10T08:00:00Z',
  ...over,
})

describe('QueueSidebar', () => {
  it('按传入顺序渲染（后端已排序，前端不重排）', () => {
    const w = mount(QueueSidebar, {
      props: {
        items: [
          item({ session_id: 'high', priority: 'high' }),
          item({ session_id: 'low', priority: 'low' }),
        ],
      },
    })
    const ids = w.findAll('li').map((li) => li.attributes('data-test'))
    expect(ids).toEqual(['queue-high', 'queue-low'])
  })

  it('高风险项带红色标记类', () => {
    const w = mount(QueueSidebar, {
      props: { items: [item({ session_id: 'a', priority: 'high' }), item({ session_id: 'b' })] },
    })
    expect(w.find('[data-test="queue-a"]').classes()).toContain('item--high')
    expect(w.find('[data-test="queue-b"]').classes()).not.toContain('item--high')
  })

  it('展示摘要与优先级标签', () => {
    const w = mount(QueueSidebar, { props: { items: [item({})] } })
    expect(w.text()).toContain('用户诉求：退款')
    expect(w.text()).toContain('中')
  })

  it('空队列显示提示', () => {
    const w = mount(QueueSidebar, { props: { items: [] } })
    expect(w.text()).toContain('暂无排队会话')
  })

  it('点击 emit select', async () => {
    const w = mount(QueueSidebar, { props: { items: [item({})] } })
    await w.find('[data-test="queue-s1"]').trigger('click')
    expect(w.emitted('select')?.[0]).toEqual(['s1'])
  })
})
