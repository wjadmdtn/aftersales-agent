import type { Envelope } from '@/types'

export type ConnState = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface ReconnectPolicy {
  base_s: number
  max_s: number
}

interface Options {
  path: string
  reconnect?: ReconnectPolicy
  onFrame: (env: Envelope<any>) => void
  onStateChange?: (s: ConnState) => void
  /** 重连成功后回调，用于按 after_seq 补齐 */
  onReopen?: () => void
}

/**
 * WS 客户端。
 * 退避参数以后端 hello 下发的为准，本地常量只作连上之前的兜底。
 */
export class Socket {
  private ws: WebSocket | null = null
  private attempt = 0
  private timer: number | null = null
  private manualClose = false
  private policy: ReconnectPolicy = { base_s: 1, max_s: 30 }
  private openedOnce = false

  constructor(private opts: Options) {}

  get stateWord(): ConnState {
    if (!this.ws) return 'closed'
    return this.openedOnce ? 'open' : 'connecting'
  }

  connect(): void {
    this.manualClose = false
    this.open()
  }

  private open(): void {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    this.ws = new WebSocket(`${proto}://${location.host}${this.opts.path}`)
    this.emitState(this.openedOnce ? 'reconnecting' : 'connecting')

    this.ws.onmessage = (ev) => {
      let env: Envelope<any>
      try {
        env = JSON.parse(ev.data)
      } catch {
        return
      }
      if (env.type === 'hello') {
        const p = env.payload as any
        if (p?.reconnect) this.policy = p.reconnect
        this.attempt = 0
        this.openedOnce = true
        this.emitState('open')
        this.opts.onReopen?.()
      }
      if (env.type === 'ping') {
        this.send({ type: 'pong', payload: {} })
        return
      }
      this.opts.onFrame(env)
    }

    this.ws.onclose = () => {
      this.ws = null
      if (this.manualClose) {
        this.emitState('closed')
        return
      }
      this.scheduleReconnect()
    }
  }

  private scheduleReconnect(): void {
    this.emitState('reconnecting')
    const delay = Math.min(
      this.policy.base_s * Math.pow(2, this.attempt),
      this.policy.max_s,
    )
    // 抖动 ±20%，避免多端同时重连打满后端
    const jitter = delay * (0.8 + Math.random() * 0.4)
    this.attempt += 1
    this.timer = window.setTimeout(() => this.open(), jitter * 1000)
  }

  send(frame: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(frame))
    }
  }

  close(): void {
    this.manualClose = true
    if (this.timer) window.clearTimeout(this.timer)
    this.ws?.close()
    this.ws = null
    this.emitState('closed')
  }

  private emitState(s: ConnState): void {
    this.opts.onStateChange?.(s)
  }
}
