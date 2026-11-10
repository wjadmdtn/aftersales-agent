import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import HandoffBar from '@/components/HandoffBar.vue'
import type { UiState } from '@/types'

const global = {
  stubs: {
    ElButton: { template: '<button :disabled="disabled"><slot /></button>', props: ['disabled'] },
    ElTag: { template: '<span><slot /></span>' },
  },
}

interface BarProps {
  uiState: UiState
  agentName?: string | null
  canHandoff?: boolean
  handoffSent?: boolean
}

function bar(props: BarProps) {
  return mount(HandoffBar, { props, global })
}

describe('HandoffBar', () => {
  it('AI 服务中显示 AI 助手', () => {
    expect(bar({ uiState: 'ai_serving' }).text()).toContain('AI 助手')
  })

  it('排队中显示正在接入人工客服', () => {
    expect(bar({ uiState: 'queued' }).text()).toContain('正在接入人工客服')
  })

  it('人工服务中显示客服姓名', () => {
    expect(bar({ uiState: 'human_serving', agentName: '小王' }).text()).toContain(
      '人工客服 小王 正在为您服务',
    )
  })

  it('已解决显示会话已结束', () => {
    expect(bar({ uiState: 'resolved' }).text()).toContain('会话已结束')
  })

  it('排队与已解决时输入框禁用', async () => {
    const disabledStates: UiState[] = ['queued', 'resolved']
    for (const s of disabledStates) {
      const w = bar({ uiState: s })
      expect((w.vm as any).inputDisabled).toBe(true)
    }
    const enabledStates: UiState[] = ['ai_serving', 'human_serving']
    for (const s of enabledStates) {
      const w = bar({ uiState: s })
      expect((w.vm as any).inputDisabled).toBe(false)
    }
  })

  it('非 AI 状态不显示转人工按钮', () => {
    expect(bar({ uiState: 'queued' }).find('[data-test="quick-handoff"]').exists()).toBe(false)
    expect(bar({ uiState: 'human_serving' }).find('[data-test="quick-handoff"]').exists()).toBe(false)
    expect(bar({ uiState: 'ai_serving' }).find('[data-test="quick-handoff"]').exists()).toBe(true)
  })

  it('已提交转人工后按钮禁用', () => {
    const w = bar({ uiState: 'ai_serving', handoffSent: true })
    const btn = w.find('[data-test="quick-handoff"]')
    expect(btn.attributes('disabled')).toBeDefined()
    expect(w.text()).toContain('已提交')
  })

  it('点击转人工 emit handoff', async () => {
    const w = bar({ uiState: 'ai_serving', canHandoff: true })
    await w.find('[data-test="quick-handoff"]').trigger('click')
    expect(w.emitted('handoff')).toBeTruthy()
  })
})
