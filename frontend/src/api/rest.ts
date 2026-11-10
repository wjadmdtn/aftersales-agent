import type { BizResult, ChatState, ChatMessage, OrderData, QueueItem } from '@/types'

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url)
  return (await r.json()) as T
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  return (await r.json()) as T
}

export interface SessionSnapshot {
  state: ChatState
  messages: ChatMessage[]
  last_seq: number
  truncated: boolean
}

export const api = {
  getOrder: (orderId: string) => getJson<BizResult<OrderData>>(`/orders/${orderId}`),
  getLogistics: (orderId: string) =>
    getJson<BizResult<{ current_node: string; tracks: { node: string; at: string }[] }>>(
      `/orders/${orderId}/logistics`,
    ),
  getSession: (sessionId: string, afterSeq = 0) =>
    getJson<SessionSnapshot>(`/chat/session/${sessionId}?after_seq=${afterSeq}`),
  listSessions: (agentId: string) =>
    getJson<{ items: { session_id: string; state: string; agent_id: string | null }[] }>(
      `/chat/sessions?agent_id=${encodeURIComponent(agentId)}`,
    ),
  getQueue: () => getJson<{ items: QueueItem[]; depth: number }>('/handoff/queue'),
  accept: (sessionId: string, agentId: string) =>
    postJson<{ state: string }>('/handoff/accept', { session_id: sessionId, agent_id: agentId }),
  decline: (sessionId: string, agentId: string) =>
    postJson<{ state: string }>('/handoff/decline', { session_id: sessionId, agent_id: agentId }),
  resolve: (sessionId: string, agentId: string) =>
    postJson<{ state: string }>('/handoff/resolve', { session_id: sessionId, agent_id: agentId }),
}

/** 订单状态的中文标签，与后端 present.render 的用词保持一致 */
export const STATUS_LABEL: Record<string, string> = {
  PENDING_PAYMENT: '待支付',
  PAID: '已支付',
  SHIPPED: '已发货',
  DELIVERED: '已签收',
  CLOSED: '已关闭',
  REFUNDING: '退款中',
  REFUNDED: '已退款',
}

/** 结果码 → 用户可读文案（与后端 present.render 同口径，不另造说法） */
export function resultMessage(code: number): string {
  if (code === 2001) return '没有查到这笔订单哦，麻烦您确认一下订单号是否正确。'
  if (code === 2002) return '这笔订单还没有发货，暂时查不到物流信息。发货后您就能看到轨迹了。'
  if (code >= 4000) return '抱歉，系统出了点小状况，请您稍后再试一次。'
  return '暂时无法获取该订单。'
}
