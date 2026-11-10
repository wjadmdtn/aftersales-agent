import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'
import OrderDetail from '@/views/OrderDetail.vue'

const orderOk = {
  code: 1000,
  data: {
    order_id: 'ORD1002',
    status: 'PAID',
    amount: '259.90',
    address: {
      receiver: '李四',
      phone: '13900139000',
      province: '浙江省',
      city: '杭州市',
      district: '西湖区',
      detail: '文三路2号',
    },
    created_at: '2026-11-06T20:30:00+08:00',
    product_type: 'apparel',
  },
}

function mockFetch(order: unknown, logistics?: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) => {
      const body = url.includes('/logistics') ? logistics : order
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) })
    }),
  )
}

const router = createRouter({
  history: createMemoryHistory(),
  routes: [{ path: '/chat', name: 'chat', component: { template: '<div />' } }],
})

const global = {
  plugins: [router],
  stubs: {
    ElButton: { template: '<button><slot /></button>' },
    ElTag: { template: '<span><slot /></span>' },
  },
}

async function flush() {
  await new Promise((r) => setTimeout(r, 0))
}

describe('OrderDetail', () => {
  it('展示订单号、状态、金额、收货地址、下单时间', async () => {
    mockFetch(orderOk, { code: 2002 })
    const w = mount(OrderDetail, { props: { orderId: 'ORD1002' }, global })
    await flush()
    await w.vm.$nextTick()
    const text = w.text()
    expect(text).toContain('ORD1002')
    expect(text).toContain('259.90')
    expect(text).toContain('李四')
    expect(text).toContain('杭州市')
  })

  it('无物流时显示暂无物流信息', async () => {
    mockFetch(orderOk, { code: 2002 })
    const w = mount(OrderDetail, { props: { orderId: 'ORD1002' }, global })
    await flush()
    await w.vm.$nextTick()
    expect(w.text()).toContain('暂无物流信息')
  })

  it('订单不存在时显示错误态', async () => {
    mockFetch({ code: 2001 })
    const w = mount(OrderDetail, { props: { orderId: 'NOPE' }, global })
    await flush()
    await w.vm.$nextTick()
    expect(w.text()).toContain('没有查到这笔订单')
  })

  it('不编造商品名', async () => {
    mockFetch(orderOk, { code: 2002 })
    const w = mount(OrderDetail, { props: { orderId: 'ORD1002' }, global })
    await flush()
    await w.vm.$nextTick()
    expect(w.text()).not.toMatch(/商品名称[:：]/)
  })

  it('点客服按钮 emit contact', async () => {
    mockFetch(orderOk, { code: 2002 })
    const w = mount(OrderDetail, { props: { orderId: 'ORD1002' }, global })
    await flush()
    await w.vm.$nextTick()
    await w.find('[data-test="contact-agent"]').trigger('click')
    expect(w.emitted('contact')?.[0]).toEqual(['ORD1002'])
  })
})
