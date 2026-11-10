import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import MessageStream from '@/components/MessageStream.vue'
import type { ChatMessage } from '@/types'

const base = (over: Partial<ChatMessage> = {}): ChatMessage => ({
  id: 'm1',
  session_id: 's1',
  seq: 1,
  role: 'user',
  sender_id: 'u1',
  text: '你好',
  streaming: false,
  final: true,
  ts: '2026-11-10T08:00:00Z',
  trace_id: null,
  ...over,
})

describe('MessageStream', () => {
  it('渲染用户与 AI 消息', () => {
    const w = mount(MessageStream, {
      props: { messages: [base(), base({ id: 'm2', seq: 2, role: 'assistant', text: '您好' })] },
    })
    expect(w.text()).toContain('你好')
    expect(w.text()).toContain('您好')
  })

  it('正在流式时显示进行中的文本', () => {
    const w = mount(MessageStream, {
      props: {
        messages: [base()],
        pending: { msg_id: 'p1', seq: 2, text: '正在输', streaming: false },
      },
    })
    expect(w.text()).toContain('正在输')
  })

  it('降级时展示统一话术与转人工按钮', () => {
    const w = mount(MessageStream, {
      props: { messages: [base()], degraded: true },
      global: { stubs: { ElButton: { template: '<button><slot /></button>' } } },
    })
    expect(w.text()).toContain('系统暂时有点繁忙')
    expect(w.find('[data-test="handoff-after-degrade"]').exists()).toBe(true)
  })

  it('降级按钮 emit handoff', async () => {
    const w = mount(MessageStream, {
      props: { messages: [base()], degraded: true },
      global: { stubs: { ElButton: { template: '<button><slot /></button>' } } },
    })
    await w.find('[data-test="handoff-after-degrade"]').trigger('click')
    expect(w.emitted('handoff')).toBeTruthy()
  })

  it('中断后已输出内容保留', () => {
    const w = mount(MessageStream, {
      props: { messages: [base({ role: 'assistant', text: '已输出的半句' })], interrupted: true },
    })
    expect(w.text()).toContain('已输出的半句')
    expect(w.text()).toContain('重试')
  })
})
