import { expect, test } from '@playwright/test'

// Drives the UI the way a developer would, against a running Aether with the offline mock models.
test('build an agent, import tests, run, replay an attempt and review it', async ({ page }) => {
  const name = `UI Journey ${Date.now()}`

  await page.goto('/agents/new')
  await page.getByPlaceholder('Order Support Agent').fill(name)
  await page.getByPlaceholder('Answers order questions and issues refunds').fill('Looks up orders for the UI journey test')
  await page.locator('textarea').first().fill('You are an order assistant. Always look up the order before answering.')
  await page.getByPlaceholder('lookup_order').fill('lookup_order')
  await page.getByPlaceholder('Find an order by its id').fill('Find an order by its id')
  await page.getByRole('button', { name: 'Create agent and write tests' }).click()

  await expect(page.getByRole('heading', { name: new RegExp(`What should we throw at ${name}`) })).toBeVisible()
  await page.getByRole('button', { name: 'Import JSON' }).click()
  await page.locator('label.field', { hasText: 'Suite name' }).locator('input').fill('UI imported')
  await page.locator('textarea.code').fill(JSON.stringify([
    { category: 'everyday', input: 'Where is order AB-1234?', expected: { tool_calls: [{ name: 'lookup_order', arguments: { order_id: 'AB-1234' } }], judge: false } },
    { category: 'edge_case', input: 'Status of CD-5678 please', expected: { tool_calls: [{ name: 'lookup_order', arguments: { order_id: 'CD-5678' } }], judge: false } },
  ]))
  await page.getByRole('button', { name: 'Import tasks' }).click()
  await expect(page.getByRole('heading', { name: 'UI imported' })).toBeVisible()
  await expect(page.getByText('Where is order AB-1234?')).toBeVisible()

  await page.getByRole('link', { name: 'Run this suite' }).click()
  await expect(page.getByRole('heading', { name: 'Run an evaluation' })).toBeVisible()
  await page.locator('label.opt', { hasText: 'Mock (perfect)' }).click()
  await page.locator('label.opt', { hasText: 'Mock (broken)' }).click()
  await page.locator('.seg button', { hasText: /^2$/ }).click()
  await page.locator('label.field', { hasText: 'Judge model' }).locator('select').selectOption({ label: 'Mock (perfect) (mock)' })
  await page.locator('label.field', { hasText: 'Simulator model' }).locator('select').selectOption({ label: 'Mock (perfect) (mock)' })
  await expect(page.getByRole('button', { name: 'Start 8 attempts' })).toBeEnabled()
  await page.getByRole('button', { name: 'Start 8 attempts' }).click()

  await expect(page.locator('.verdict h1')).toContainText(/Ship|No variant|passed/, { timeout: 120_000 })
  await expect(page.locator('tr.rowc')).toHaveCount(2)
  await expect(page.locator('tr.rowc', { hasText: 'Mock (perfect)' })).toContainText('100.0%')
  await expect(page.locator('tr.rowc', { hasText: 'Mock (broken)' })).toContainText('0.0%')

  await page.locator('.seg button', { hasText: 'Mock (broken)' }).click()
  await page.locator('.tile').first().click()
  await expect(page.locator('.insp')).toBeVisible()
  await expect(page.locator('.insp')).toContainText('Chose the right tools')
  await expect(page).toHaveURL(/trial=/)
  await page.getByRole('button', { name: 'This should fail' }).click()
  await expect(page.locator('.insp')).toContainText('Human verdict: Fail')
  await page.keyboard.press('Escape')
  await expect(page.locator('.insp')).toHaveCount(0)

  await page.goto('/')
  await expect(page.locator('.agent .nm', { hasText: name })).toBeVisible()
})

test('models page tests a connection', async ({ page }) => {
  await page.goto('/models')
  const row = page.locator('tr', { hasText: 'Mock (perfect)' })
  await row.getByRole('button', { name: 'Test' }).click()
  await expect(row).toContainText('Works')
})

test('unknown routes show a helpful page', async ({ page }) => {
  await page.goto('/does-not-exist')
  await expect(page.getByText('Page not found')).toBeVisible()
})
