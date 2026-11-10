import { expect, test } from '@playwright/test'

/**
 * 演示路径 ⓪~⑨（SC-001）。走真实后端 + 真实 WebSocket。
 * 断言用 contains 而非全等：后端话术与 spec 文本不完全一致（plan Q2）。
 */
test('演示路径：订单页 → 客服 → 流式 → 转人工 → 工作台接管 → 结束', async ({ browser }) => {
  const userCtx = await browser.newContext()
  const agentCtx = await browser.newContext()
  const user = await userCtx.newPage()
  const agent = await agentCtx.newPage()

  // ---- ⓪ 订单详情页 ----
  await user.goto('/#/order/ORD1002')
  await expect(user.getByTestId('order-detail')).toBeVisible()
  await expect(user.getByText('ORD1002')).toBeVisible()
  await expect(user.getByText('259.90')).toBeVisible()
  await expect(user.getByText('李四')).toBeVisible()

  // 人工工作台先连上，才能收到队列推送
  await agent.goto('/#/console')
  await expect(agent.getByText('排队中')).toBeVisible()

  // ---- ① 点客服，订单随会话带入 ----
  await user.getByTestId('contact-agent').click()
  await expect(user.getByTestId('bound-order')).toContainText('ORD1002')
  // 等 WS 握手完成再发消息，否则发送会被丢弃
  await expect(user.getByTestId('connection-banner')).toHaveCount(0)

  await user.getByTestId('chat-input').fill('我要退款')
  await user.getByTestId('chat-send').click()

  // ---- ② AI 复述订单与金额，不追问订单号 ----
  await expect(user.getByText(/即将为订单 ORD1002/)).toBeVisible()
  await expect(user.getByText(/259\.90/)).toBeVisible()

  // ---- ③ 确认 → 已提交 ----
  await user.getByTestId('chat-input').fill('确认')
  await user.getByTestId('chat-send').click()
  await expect(user.getByText(/已为您提交退款/)).toBeVisible()

  // ---- ④ 投诉 → 转人工 ----
  await user.getByTestId('chat-input').fill('我要投诉')
  await user.getByTestId('chat-send').click()
  await expect(user.getByTestId('state-label')).toContainText('正在接入人工客服')

  // ---- ⑤ 工作台出现高风险会话 ----
  const queued = agent.locator('li.item--high').first()
  await expect(queued).toBeVisible()

  // ---- ⑥ 上下文包 ----
  await queued.click()
  await expect(agent.getByTestId('context-pack')).toBeVisible()
  await expect(agent.getByTestId('summary')).toContainText('用户诉求')
  await expect(agent.getByTestId('priority-reason')).toContainText('风险触发')
  await expect(agent.getByTestId('trigger-type')).toContainText('风险拦截')
  // FR-012：风险场景不显示 AI 建议
  await expect(agent.getByTestId('ai-suggestion')).toHaveCount(0)

  // ---- ⑦ 接入 → 用户端看到人工客服 ----
  await agent.getByTestId('accept').click()
  await expect(user.getByTestId('state-label')).toContainText('人工客服 小王')

  // ---- ⑧ 人工发消息 → 用户端收到 ----
  await agent.getByTestId('agent-input').fill('您好，我来处理 ♥')
  await agent.getByTestId('agent-send').click()
  await expect(user.getByText('您好，我来处理 ♥')).toBeVisible()

  // 用户回复 → 人工端收到
  await user.getByTestId('chat-input').fill('好的谢谢')
  await user.getByTestId('chat-send').click()
  await expect(agent.getByText('好的谢谢')).toBeVisible()

  // ---- ⑨ 结束会话 → 双方已解决 ----
  await agent.getByTestId('resolve').click()
  await expect(user.getByTestId('state-label')).toContainText('会话已结束')

  await userCtx.close()
  await agentCtx.close()
})
