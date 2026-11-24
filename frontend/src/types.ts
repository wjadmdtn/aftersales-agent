export type Role = 'user' | 'assistant' | 'human' | 'system'

export interface ChatMessage {
  id: string
  session_id: string
  seq: number
  role: Role
  sender_id: string | null
  text: string
  streaming: boolean
  final: boolean
  ts: string
  trace_id: string | null
}

export type UiState = 'ai_serving' | 'queued' | 'human_serving' | 'resolved'

export interface ChatState {
  session_id: string
  user_id: string
  state: string
  ui_state: UiState
  agent_id: string | null
  agent_name: string | null
  order_id: string | null
  updated_at: string
  last_seq: number
}

export interface Envelope<P = Record<string, unknown>> {
  type: string
  session_id: string | null
  seq: number
  ts: string
  payload: P
}

export interface OrderData {
  order_id: string
  status: string
  amount: string
  address: {
    receiver: string
    phone: string
    province: string
    city: string
    district: string
    detail: string
  }
  created_at: string
  product_type?: string | null
}

export interface BizResult<T> {
  code: number
  data?: T
  reason?: string | null
  meta?: Record<string, unknown>
}

export interface QueueItem {
  session_id: string
  summary: string
  priority: 'high' | 'medium' | 'low'
  priority_reason: string
  trigger_type: string
  trigger_detail: string
  suggested_next_step: string | null
  primary_intent: string
  sentiment: string
  order_id: string | null
  queued_at: string
}

/** GET /chat/sessions?agent_id= 的条目：我已接入的会话 */
export interface MySession {
  session_id: string
  state: string
  agent_id: string | null
  agent_name: string | null
  order_id: string | null
  last_message: string | null
  last_ts: string
}

export interface PendingAssistant {
  msg_id: string
  seq: number
  text: string
  streaming: boolean
}

/** 服务端下发的确认卡（退款/改地址）：用户只能点按钮确认或取消 */
export interface ConfirmCard {
  card_id: string
  action: 'refund' | 'change_address'
  order_id: string
  amount: string | null
  summary: string
  expires_at: string
}
