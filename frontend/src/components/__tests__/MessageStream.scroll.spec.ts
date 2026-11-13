import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import MessageStream from '@/components/MessageStream.vue'
import type { ChatMessage } from '@/types'

function msg(seq: number, text: string, role: 'user' | 'assistant' = 'user'): ChatMessage {
  return {
    id: `m${seq}`,
    session_id: 's1',
    seq,
    role,
    sender_id: null,
    text,
    streaming: false,
    final: true,
    ts: '',
    trace_id: null,
  }
}

/** jsdom 不做布局，scrollHeight 恒为 0，这里打桩模拟内容撑满。 */
function stubLayout(el: HTMLElement, { height = 1000, viewport = 300 } = {}) {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, value: height })
  Object.defineProperty(el, 'clientHeight', { configurable: true, value: viewport })
  el.scrollTo = vi.fn() as any
}

describe('MessageStream 自动滚动', () => {
  it('新消息到达时滚到底部', async () => {
    const w = mount(MessageStream, { props: { messages: [msg(1, 'a')] } })
    const el = w.find('[data-test="message-stream"]').element as HTMLElement
    stubLayout(el)

    await w.setProps({ messages: [msg(1, 'a'), msg(2, 'b')] })
    await flushPromises()
    expect(el.scrollTo).toHaveBeenCalled()
  })

  it('用户往上翻看历史时不强行拽回底部', async () => {
    const w = mount(MessageStream, { props: { messages: [msg(1, 'a')] } })
    const el = w.find('[data-test="message-stream"]').element as HTMLElement
    stubLayout(el)

    // 模拟用户向上滚动（距底部很远）
    Object.defineProperty(el, 'scrollTop', { configurable: true, value: 100 })
    await w.find('[data-test="message-stream"]').trigger('scroll')
    ;(el.scrollTo as any).mockClear()

    await w.setProps({ messages: [msg(1, 'a'), msg(2, 'b')] })
    await flushPromises()
    expect(el.scrollTo).not.toHaveBeenCalled()
  })

  it('出现等待占位时回到底部（用户刚发消息）', async () => {
    const w = mount(MessageStream, { props: { messages: [msg(1, 'a')] } })
    const el = w.find('[data-test="message-stream"]').element as HTMLElement
    stubLayout(el)
    Object.defineProperty(el, 'scrollTop', { configurable: true, value: 0 })
    await w.find('[data-test="message-stream"]').trigger('scroll')
    ;(el.scrollTo as any).mockClear()

    await w.setProps({
      pending: { msg_id: 'p1', seq: 0, text: '', streaming: true },
    })
    await flushPromises()
    expect(el.scrollTo).toHaveBeenCalled()
  })
})
