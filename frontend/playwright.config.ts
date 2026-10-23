import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 180_000,
  expect: { timeout: 20_000 },
  retries: 0,
  use: {
    baseURL: process.env.AETHER_UI_URL ?? 'http://localhost:5173',
    channel: process.env.PW_CHANNEL ?? 'chrome',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
