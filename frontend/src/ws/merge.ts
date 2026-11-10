import type { ChatMessage } from '@/types'

/**
 * 按 (session_id, seq) 去重并按 seq 排序。
 * seq 跳号时进入缓冲窗口：网络乱序是正常的，但长时间缺号说明丢帧，
 * 由调用方触发 REST 补齐（FR-027）。
 */
export interface MergeResult {
  messages: ChatMessage[]
  gapAt: number | null
}

export function mergeMessages(
  existing: ChatMessage[],
  incoming: ChatMessage[],
): MergeResult {
  const byKey = new Map<string, ChatMessage>()
  for (const m of existing) byKey.set(`${m.session_id}:${m.seq}`, m)
  for (const m of incoming) byKey.set(`${m.session_id}:${m.seq}`, m)

  const merged = [...byKey.values()].sort((a, b) => a.seq - b.seq)

  let gapAt: number | null = null
  const seen = new Set<number>()
  for (const m of merged) seen.add(m.seq)
  const max = merged.length ? merged[merged.length - 1].seq : 0
  for (let s = 1; s <= max; s++) {
    if (!seen.has(s)) {
      gapAt = s
      break
    }
  }
  return { messages: merged, gapAt }
}
