import { describe, expect, it } from 'vitest'
import { mergeMessages } from '@/ws/merge'
import type { ChatMessage } from '@/types'

const m = (seq: number, text = `m${seq}`): ChatMessage => ({
  id: `id${seq}`,
  session_id: 's1',
  seq,
  role: 'user',
  sender_id: 'u1',
  text,
  streaming: false,
  final: true,
  ts: '2026-11-10T08:00:00Z',
  trace_id: null,
})

describe('mergeMessages', () => {
  it('按 seq 排序', () => {
    const { messages } = mergeMessages([m(3), m(1)], [m(2)])
    expect(messages.map((x) => x.seq)).toEqual([1, 2, 3])
  })

  it('相同 seq 只保留一条（后到覆盖）', () => {
    const { messages } = mergeMessages([m(1, 'old')], [m(1, 'new')])
    expect(messages).toHaveLength(1)
    expect(messages[0].text).toBe('new')
  })

  it('无缺口时 gapAt 为 null', () => {
    expect(mergeMessages([], [m(1), m(2), m(3)]).gapAt).toBeNull()
  })

  it('缺号时报告第一个缺口', () => {
    expect(mergeMessages([], [m(1), m(3)]).gapAt).toBe(2)
  })

  it('空输入不报缺口', () => {
    expect(mergeMessages([], []).gapAt).toBeNull()
  })

  it('重复投递不产生重复行', () => {
    const { messages } = mergeMessages([m(1), m(2)], [m(2), m(2)])
    expect(messages).toHaveLength(2)
  })
})
