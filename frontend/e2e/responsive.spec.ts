import { expect, test } from '@playwright/test'

/**
 * SC-005：1280×720 与 1920×1080 下两个页面均可正常使用。
 * 判据：无横向滚动、关键元素可见。
 */
const SIZES = [
  { w: 1280, h: 720 },
  { w: 1920, h: 1080 },
]

for (const size of SIZES) {
  test(`订单页 ${size.w}x${size.h} 可用`, async ({ page }) => {
    await page.setViewportSize({ width: size.w, height: size.h })
    await page.goto('/#/order/ORD1002')
    await expect(page.getByTestId('order-detail')).toBeVisible()
    await expect(page.getByTestId('contact-agent')).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(overflow, '不应出现横向滚动').toBe(false)
  })

  test(`对话页 ${size.w}x${size.h} 可用`, async ({ page }) => {
    await page.setViewportSize({ width: size.w, height: size.h })
    await page.goto('/#/order/ORD1002')
    await page.getByTestId('contact-agent').click()
    await expect(page.getByTestId('bound-order')).toBeVisible()
    await expect(page.getByTestId('chat-input')).toBeVisible()
    await expect(page.getByTestId('state-label')).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(overflow, '不应出现横向滚动').toBe(false)
  })

  test(`工作台 ${size.w}x${size.h} 可用`, async ({ page }) => {
    await page.setViewportSize({ width: size.w, height: size.h })
    await page.goto('/#/console')
    await expect(page.getByText('排队中')).toBeVisible()

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    )
    expect(overflow, '不应出现横向滚动').toBe(false)
  })
}
