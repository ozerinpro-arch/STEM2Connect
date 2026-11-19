import assert from 'node:assert/strict'
import { spawn, execFileSync } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'

const liveModel = process.env.RESEARCHLENS_TEST_LIVE_MODEL === '1'
const root = fileURLToPath(new URL('../../', import.meta.url))
const python = process.env.PYTHON || join(root, '.venv/bin/python')
const temporary = await mkdtemp(join(tmpdir(), 'researchlens-browser-'))
const probe = createServer()
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve))
const port = probe.address().port
await new Promise((resolve, reject) => probe.close(error => error ? reject(error) : resolve()))
const base = `http://127.0.0.1:${port}`
const server = spawn(python, ['-m', 'uvicorn', 'backend.main:api', '--port', String(port), '--no-access-log'], {
  cwd: root, stdio: 'inherit',
  env: {...process.env, RESEARCHLENS_MODEL_URL: liveModel ? process.env.RESEARCHLENS_MODEL_URL : '', RESEARCHLENS_SECURE_COOKIE: '0'},
})
const serverDone = new Promise(resolve => server.once('close', resolve))
let browser

async function visible(locator) {
  await locator.waitFor({state: 'visible'})
}

async function accessible(page) {
  const result = await new AxeBuilder({page}).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()
  assert.deepEqual(result.violations.map(item => [item.id, item.nodes.map(node => node.target)]), [])
}

try {
  for (let i = 0; i < 60; i++) {
    if (await fetch(base + '/api/v1/health').then(response => response.ok, () => false)) break
    if (i === 59 || server.exitCode !== null) throw new Error('Backend did not start.')
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  browser = await chromium.launch({channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true})
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, colorScheme: 'light'})
  const page = await context.newPage()
  page.setDefaultTimeout(8000)

  const crashes = []
  page.on('pageerror', error => crashes.push(error.message))
  await page.goto(base)
  await page.keyboard.press('Tab')
  assert.equal(await page.locator(':focus').textContent(), 'Skip to workspace')
  await visible(page.getByRole('heading', {name: 'How much does the sample shape the result?'}))
  await accessible(page)
  await page.getByRole('button', {name: /Search benchmark/}).click()
  await visible(page.getByRole('heading', {name: 'The ranking reverses'}))
  await page.getByRole('button', {name: /Standardized, group A, 55%/}).click()
  await visible(page.getByText('0.5*(9/10)+0.5*(18/90)', {exact: true}))
  await page.getByLabel('Stratum', {exact: true}).selectOption('hard')
  await visible(page.getByRole('button', {name: /hard, group A, 20%/}))
  const composition = page.getByRole('region', {name: 'Sample composition'})
  await visible(composition.getByText('90 of 100 observations', {exact: true}))
  await visible(composition.getByText('10 of 100 observations', {exact: true}))
  await page.getByText('View all estimates as a table', {exact: true}).click()
  await visible(page.getByRole('region', {name: 'All estimates'}).getByRole('table'))
  await accessible(page)
  await page.getByLabel('Theme', {exact: true}).selectOption('dark')
  await accessible(page)
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({width, height: 900})
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true,
      `Horizontal page overflow at ${width}px`)
  }
  await page.setViewportSize({width: 390, height: 844})
  await page.getByRole('button', {name: /Standardized, group B, 45%/}).focus()
  await page.keyboard.press('Enter')
  await page.locator('[aria-label="Estimate details"]:focus').waitFor()
  await page.reload()
  await visible(page.getByRole('heading', {name: 'The ranking reverses'}))
  assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark')

  await page.locator('input[type=file]').setInputFiles({
    name: 'invalid.csv', mimeType: 'text/csv', buffer: Buffer.from('x,y\n1\n'),
  })
  await visible(page.getByRole('alert').filter({hasText: 'wrong number of fields'}))
  await page.locator('input[type=file]').setInputFiles(join(root, 'contracts/fixtures/stable-search.csv'))
  await visible(page.getByRole('heading', {name: 'Define the comparison'}))
  await page.getByLabel('Research claim').fill('System A has a higher success rate than B')
  await page.getByLabel('Group 1', {exact: true}).fill('A')
  await page.getByLabel('Group 2', {exact: true}).fill('B')
  if (await page.getByLabel('Stratum 2', {exact: true}).count() === 0) {
    await page.getByRole('button', {name: 'Add stratum'}).click()
    await page.getByLabel('Stratum 2', {exact: true}).fill('hard')
    await page.getByRole('button', {name: 'Equal weights'}).click()
  }
  await accessible(page)
  await page.getByRole('button', {name: /Confirm plan & run/}).click()
  await visible(page.getByRole('heading', {name: 'The ranking holds'}))
  await visible(page.getByRole('button', {name: /Observed overall, group A, 85%/}))
  await visible(page.getByRole('button', {name: /Standardized, group B, 65%/}))
  assert.equal(await page.getByRole('button', {name: 'Generate explanation'}).isDisabled(), true)
  await page.getByLabel('Allow sharing aggregate estimates').check()
  await page.getByRole('button', {name: 'Generate explanation'}).click()
  if (liveModel) {
    await page.getByText('Group aliases:', {exact: false}).waitFor({timeout: 48_000})
    const citation = page.getByRole('button', {name: /Observed overall · A/}).first()
    await citation.click()
    await visible(page.getByRole('complementary', {name: 'Estimate details'}).getByText('17/20', {exact: true}))
    await page.reload()
    await page.getByText('Group aliases:', {exact: false}).waitFor({timeout: 12_000})
  } else {
    await visible(page.getByText('The last explanation request failed.', {exact: false}))
  }
  await visible(page.getByRole('heading', {name: 'The ranking holds'}))

  await page.getByLabel('Include the source CSV').check()
  const download = page.waitForEvent('download')
  await page.getByRole('button', {name: 'Download ZIP'}).click()
  const zip = join(temporary, 'experiment.zip')
  await (await download).saveAs(zip)
  execFileSync(python, ['-m', 'zipfile', '-e', zip, temporary])
  const recalculated = JSON.parse(execFileSync(python, ['-E', '-S', 'reproduce.py'], {cwd: temporary}).toString())
  assert.equal(recalculated.estimates.find(estimate => estimate.id === 'observed_A_all').value, .85)

  await page.getByRole('button', {name: 'Edit plan'}).click()
  await page.getByLabel('Weight for stratum 1', {exact: true}).fill('0.25')
  await page.getByLabel('Weight for stratum 2', {exact: true}).fill('0.75')
  await page.getByRole('button', {name: /Confirm plan & run/}).click()
  await visible(page.getByRole('heading', {name: 'The ranking holds'}))
  await visible(page.getByRole('button', {name: /Standardized, group A, 82.5%/}))
  await page.getByRole('button', {name: 'Edit plan'}).click()
  await page.getByLabel('Weight for stratum 1', {exact: true}).fill('0.004')
  await page.getByLabel('Weight for stratum 2', {exact: true}).fill('0.996')
  await page.getByRole('button', {name: /Confirm plan & run/}).click()
  await visible(page.getByRole('button', {name: /Standardized, group A, 80.04%/}))
  const slider = page.locator('input[type=range]:enabled')
  assert.equal(await slider.getAttribute('aria-valuetext'), '0.4% easy, 99.6% hard')
  await slider.press('Home')
  await page.getByRole('button', {name: 'Apply target mix'}).click()
  await visible(page.getByRole('button', {name: /Standardized, group A, 80%/}))
  const changed = page.getByRole('table', {name: 'Standardized rates after reweighting'})
  await visible(changed.getByRole('row').filter({hasText: '80.04%'}))
  const ids = await page.getByLabel('Recent experiments').locator('option').evaluateAll(options => options.map(option => option.value))
  await page.getByLabel('Recent experiments').selectOption(ids.at(-1))
  await visible(page.getByRole('heading', {name: 'The ranking reverses'}))
  await page.locator('input[type=file]').setInputFiles({
    name: 'late-stratum.csv', mimeType: 'text/csv',
    buffer: Buffer.from('system,difficulty,success\n' + 'A,easy,1\nB,easy,0\n'.repeat(10)
      + 'A,challenge,0\nB,challenge,1\n'),
  })
  await page.getByLabel('Research claim').fill('Compare the complete dataset')
  await page.getByRole('button', {name: /Confirm plan & run/}).click()
  await visible(page.getByRole('alert').filter({hasText: 'additional strata: challenge'}))
  await page.getByRole('alert').getByRole('button', {name: 'Edit plan'}).click()
  assert.equal(await page.getByLabel('Stratum 2', {exact: true}).inputValue(), 'challenge')
  await page.getByRole('button', {name: 'Equal weights'}).click()
  await page.getByRole('button', {name: /Confirm plan & run/}).click()
  await visible(page.getByRole('button', {name: /Standardized, group A, 50%/}))
  assert.deepEqual(crashes, [])
  console.log('Browser workflow passed: examples, CSV validation, import, plan, charts, keyboard, both themes, four viewport widths, AI, reload, ZIP reproduction, and history.')
} finally {
  try { await browser?.close() } finally {
    server.kill()
    await serverDone
    await rm(temporary, {recursive: true, force: true})
  }
}
