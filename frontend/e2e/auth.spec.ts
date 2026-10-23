import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

// Runs only against an installation with AETHER_AUTH_MODE=api_key: AETHER_UI_KEY=ak_... npx playwright test e2e/auth.spec.ts
const KEY = process.env.AETHER_UI_KEY

test('signing in with an API key unlocks the app, including downloads', async ({ page, request }) => {
  test.skip(!KEY, 'set AETHER_UI_KEY to run against an auth-enabled install')
  await page.goto('/')
  await expect(page.getByText('This workspace needs authentication')).toBeVisible()

  await page.goto('/settings')
  await page.getByPlaceholder(/ak_/).fill(KEY!)
  await page.getByRole('button', { name: 'Save' }).click()
  await page.goto('/')
  await expect(page.getByText('This workspace needs authentication')).toHaveCount(0)
  await expect(page.locator('.agent .nm', { hasText: 'Hotel Concierge' })).toBeVisible()

  const runs = await (await request.get('/api/v1/runs?limit=20', { headers: { Authorization: `Bearer ${KEY}` } })).json()
  const done = runs.find((r: { status: string }) => r.status === 'completed')
  await page.goto(`/runs/${done.id}`)
  const [file] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Export CSV' }).click()])
  expect(file.suggestedFilename()).toMatch(/^aether-run-\d+\.csv$/)
  const path = await file.path()
  const text = readFileSync(path, 'utf8')
  expect(text.split('\n')[0]).toContain('task_ref')
})
