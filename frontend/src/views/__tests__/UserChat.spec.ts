import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import UserChat from '@/views/UserChat.vue'

vi.mock('@/ws/socket', () => {
  class FakeSocket {
    static instances: any[] = []
    sent: any[] = []
    opts: any
    constructor(opts: any) {
      this.opts = opts
      FakeSocket.instances.push(this)
    }
    connect() {
      this.opts.onStateChange?.('open')
    }
    send(f: any) {
      this.sent.push(f)
    }
    close() {}
  }
  return { Socket: FakeSocket }
})

vi.mock('@/api/rest', () => ({
  api: {
    getSession: vi.fn().mockRejectedValue(new Error('none')),
    getOrder: vi.fn(),
  },
  STATUS_LABEL: {},
  resultMessage: () => '',
}))

const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/chat', name: 'chat', component: UserChat }],
})

const global = {
  plugins: [router],
  stubs: {
    ElButton: { template: '<button :disabled="disabled"><slot /></button>', props: ['disabled'] },
    ElTag: { template: '<span><slot /></span>' },
    ElInput: {
      props: ['modelValue', 'disabled', 'placeholder'],
      template: '<input :disabled="disabled" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
    },
  },
}

async function mountChat() {
  const w = mount(UserChat, { global })
  await flushPromises()
  return w
}

describe('UserChat 快捷转人工', () => {
  it('点击转人工发送 user.handoff', async () => {
    const w = await mountChat()
    await w.find('[data-test="quick-handoff"]').trigger('click')
    const sock = (await import('@/ws/socket')).Socket as any
    const inst = sock.instances[sock.instances.length - 1]
    expect(inst.sent.filter((f: any) => f.type === 'user.handoff')).toHaveLength(1)
  })

  it('重复点击只发一次', async () => {
    const w = await mountChat()
    await w.find('[data-test="quick-handoff"]').trigger('click')
    await w.find('[data-test="quick-handoff"]').trigger('click')
    const sock = (await import('@/ws/socket')).Socket as any
    const inst = sock.instances[sock.instances.length - 1]
    expect(inst.sent.filter((f: any) => f.type === 'user.handoff')).toHaveLength(1)
  })

  it('排队中输入框禁用且不发消息', async () => {
    const w = await mountChat()
    const sock = (await import('@/ws/socket')).Socket as any
    const inst = sock.instances[sock.instances.length - 1]

    inst.opts.onFrame({
      type: 'session.state',
      session_id: 's1',
      seq: 0,
      ts: '',
      payload: { ui_state: 'queued', state: 'queued' },
    })
    await flushPromises()

    // 状态条告知用户在排队
    expect(w.text()).toContain('正在接入人工客服')
    // 输入框禁用（FR-006）
    expect(w.find('[data-test="chat-input"]').attributes('disabled')).toBeDefined()

    await w.find('[data-test="chat-input"]').setValue('我要退款')
    await w.find('[data-test="chat-send"]').trigger('click')
    expect(inst.sent.filter((f: any) => f.type === 'user.message')).toHaveLength(0)
  })
})
