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

describe('UserChat 等待反馈', () => {
  async function send(text: string) {
    const w = await mountChat()
    await w.find('[data-test="chat-input"]').setValue(text)
    await w.find('[data-test="chat-send"]').trigger('click')
    await flushPromises()
    const sock = (await import('@/ws/socket')).Socket as any
    return { w, inst: sock.instances[sock.instances.length - 1] }
  }

  it('发送后立刻显示"正在输入"，不等首个 delta', async () => {
    // 政策链路首个响应可达 20~40s；这段期间没有任何反馈，
    // 用户会以为消息没发出去。必须点发送就出现等待态。
    const { w } = await send('退款多久到账')
    expect(w.find('[data-test="msg-pending"]').exists()).toBe(true)
    expect(w.find('[data-test="typing"]').exists()).toBe(true)
  })

  it('收到 delta 后显示文字而非三点', async () => {
    const { w, inst } = await send('退款多久到账')
    inst.opts.onFrame({
      type: 'assistant.delta',
      session_id: 's1',
      seq: 5,
      ts: '',
      payload: { msg_id: 'm1', text_delta: '银行卡 3~7 天', streaming: false },
    })
    await flushPromises()
    expect(w.find('[data-test="typing"]').exists()).toBe(false)
    expect(w.find('[data-test="msg-pending"]').text()).toContain('银行卡 3~7 天')
  })

  it('done 后等待占位移除', async () => {
    const { w, inst } = await send('退款多久到账')
    inst.opts.onFrame({
      type: 'assistant.done',
      session_id: 's1',
      seq: 5,
      ts: '',
      payload: { msg_id: 'm1', seq: 5, text: '银行卡 3~7 天', streaming: false },
    })
    await flushPromises()
    expect(w.find('[data-test="msg-pending"]').exists()).toBe(false)
    expect(w.text()).toContain('银行卡 3~7 天')
  })

  it('出错时清掉等待占位', async () => {
    // 不清掉的话界面会永远停在"正在输入"
    const { w, inst } = await send('退款多久到账')
    inst.opts.onFrame({
      type: 'error',
      session_id: 's1',
      seq: 0,
      ts: '',
      payload: { code: 'internal', message: '出错了' },
    })
    await flushPromises()
    expect(w.find('[data-test="msg-pending"]').exists()).toBe(false)
  })

  it('人工服务中发消息不显示 AI 正在输入', async () => {
    // 用户实测：人工接入后发消息，界面一直转 AI loading 停不下来。
    // 此时是人在回复，显示"AI 正在输入"既错误又永远不会结束。
    const { w, inst } = await send('退款多久到账')
    inst.opts.onFrame({
      type: 'session.state',
      session_id: 's1',
      seq: 0,
      ts: '',
      payload: { ui_state: 'human_serving', state: 'human_serving', agent_name: '小王' },
    })
    await flushPromises()

    await w.find('[data-test="chat-input"]').setValue('我没说转人工')
    await w.find('[data-test="chat-send"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-test="msg-pending"]').exists()).toBe(false)
  })

  it('切到人工时清掉残留的 AI 占位', async () => {
    const { w, inst } = await send('退款多久到账')
    expect(w.find('[data-test="msg-pending"]').exists()).toBe(true)
    inst.opts.onFrame({
      type: 'session.state',
      session_id: 's1',
      seq: 0,
      ts: '',
      payload: { ui_state: 'queued', state: 'queued' },
    })
    await flushPromises()
    expect(w.find('[data-test="msg-pending"]').exists()).toBe(false)
  })
})

describe('会话开场与空闲超时', () => {
  it('新会话进入时 AI 先打招呼', async () => {
    const w = await mountChat()
    expect(w.text()).toContain('有什么可以帮您')
  })

  it('5 分钟无互动则标记会话结束并提示', async () => {
    vi.useFakeTimers()
    try {
      const w = await mountChat()
      expect(w.find('[data-test="idle-ended"]').exists()).toBe(false)
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 100)
      await flushPromises()
      expect(w.find('[data-test="idle-ended"]').exists()).toBe(true)
      expect(w.text()).toContain('本次会话已结束')
    } finally {
      vi.useRealTimers()
    }
  })

  it('超时后发送消息即开始新会话，提示消失', async () => {
    vi.useFakeTimers()
    try {
      const w = await mountChat()
      await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 100)
      await flushPromises()
      expect(w.find('[data-test="idle-ended"]').exists()).toBe(true)

      await w.find('[data-test="chat-input"]').setValue('还在吗')
      await w.find('[data-test="chat-send"]').trigger('click')
      await flushPromises()
      expect(w.find('[data-test="idle-ended"]').exists()).toBe(false)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('UserChat 地址表单', () => {
  async function latestSocket() {
    const sock = (await import('@/ws/socket')).Socket as any
    return sock.instances[sock.instances.length - 1]
  }

  async function emitNeedAddress(inst: any, state = 'need_address') {
    inst.opts.onFrame({
      type: 'assistant.done',
      session_id: 's1',
      seq: 5,
      ts: new Date().toISOString(),
      payload: {
        msg_id: 'm1',
        seq: 5,
        text: '好的，请填写新的收货信息',
        state,
        intent: 'change_address',
        tool_calls: [],
        degraded: false,
      },
    })
    await flushPromises()
  }

  it('need_address 时弹出表单，提交后发出 user.address', async () => {
    const w = await mountChat()
    const inst = await latestSocket()
    await emitNeedAddress(inst)
    expect(w.find('[data-test="address-form"]').exists()).toBe(true)

    await w.find('[data-test="addr-receiver"]').setValue('张三')
    await w.find('[data-test="addr-phone"]').setValue('13800138000')
    await w.find('[data-test="addr-province"]').setValue('北京市')
    await w.find('[data-test="addr-city"]').setValue('北京市')
    await w.find('[data-test="addr-district"]').setValue('朝阳区')
    await w.find('[data-test="addr-detail"]').setValue('建国路4号')
    await w.find('[data-test="address-form"]').trigger('submit')
    await flushPromises()

    const sent = inst.sent.filter((f: any) => f.type === 'user.address')
    expect(sent).toHaveLength(1)
    expect(sent[0].payload.address).toEqual({
      receiver: '张三',
      phone: '13800138000',
      province: '北京市',
      city: '北京市',
      district: '朝阳区',
      detail: '建国路4号',
    })
    // 提交后收起表单，等后端回确认卡
    expect(w.find('[data-test="address-form"]').exists()).toBe(false)
  })

  it('字段不全时不发请求', async () => {
    const w = await mountChat()
    const inst = await latestSocket()
    await emitNeedAddress(inst)

    await w.find('[data-test="addr-receiver"]').setValue('张三')
    await w.find('[data-test="address-form"]').trigger('submit')
    await flushPromises()

    expect(inst.sent.filter((f: any) => f.type === 'user.address')).toHaveLength(0)
    expect(w.find('[data-test="addr-error"]').exists()).toBe(true)
  })

  it('非 need_address 状态不显示表单', async () => {
    const w = await mountChat()
    const inst = await latestSocket()
    await emitNeedAddress(inst, 'inquiry_answered')
    expect(w.find('[data-test="address-form"]').exists()).toBe(false)
  })
})
