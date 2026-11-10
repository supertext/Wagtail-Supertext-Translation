#!/usr/bin/env node
/**
 * Regenerates docs/images from a freshly set-up demo whose translator talks to stand-in.mjs.
 * See docs/DEVELOPER.md -> Docs screenshots.
 *
 *   BASE_URL (default http://127.0.0.1:8093)
 *   DEMO_ADMIN_EMAIL / DEMO_ADMIN_PASSWORD     settings screens (superuser)
 *   DEMO_EDITOR_EMAIL / DEMO_EDITOR_PASSWORD   translating (Moderators group)
 */
import { readFileSync } from 'node:fs'
import { chromium } from 'playwright'

const B = process.env.BASE_URL || 'http://127.0.0.1:8093'
const OUT = new URL('../../docs/images', import.meta.url).pathname
const LIVE_API = 'https://api.supertext.com/v1/'
const FIXED_TIME = 'Oct. 13, 2026, 9:41 a.m.' // Django's DATETIME_FORMAT in English
const pad = (r, p = 8) => ({ x: Math.max(0, r.x - p), y: Math.max(0, r.y - p), width: r.width + 2 * p, height: r.height + 2 * p })

const browser = await chromium.launch()

/** An exported HTML file "translated" with samples.json (what the translator would send back). */
const SAMPLES = JSON.parse(readFileSync(new URL('samples.json', import.meta.url), 'utf8'))
const norm = (s) => s.replace(/\s+/g, ' ').replace(/> </g, '><').trim()
const decode = (s) => s.replace(/&quot;/g, '"').replace(/&#0?39;|&apos;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
const encode = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
function translateHtml(html, lang) {
  const known = new Map(Object.entries(SAMPLES[lang]).map(([k, v]) => [norm(k), v]))
  return html.replace(/(<div data-st-id="[^"]+">)([\s\S]*?)(<\/div>)/g, (all, open, inner, close) => {
    const hit = known.get(norm(inner)) ?? known.get(norm(decode(inner)))
    return hit === undefined ? all : open + (known.has(norm(inner)) ? hit : encode(hit)) + close
  })
}

async function session(email, password) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } })
  // Times shown by the browser (relative dates and the like) don't change between runs.
  await context.clock.setFixedTime(new Date('2026-10-13T09:41:00+02:00'))
  const page = await context.newPage()
  await page.goto(`${B}/admin/login/`)
  await page.fill('#id_username', email)
  await page.fill('#id_password', password)
  await page.click('button[type=submit]')
  await page.waitForLoadState('networkidle')
  const shot = async (name, clip) => {
    await page.addStyleTag({ content: '*{animation:none!important;transition:none!important}' })
    await page.waitForTimeout(300)
    await page.screenshot({ path: `${OUT}/${name}.png`, ...(clip ? { clip } : {}) })
  }
  const box = async (locator) => pad(await (typeof locator === 'string' ? page.locator(locator).first() : locator).boundingBox())
  const go = async (url) => { await page.goto(B + url); await page.waitForLoadState('networkidle') }
  return { page, shot, box, go }
}

/** The id of a page's translation (the admin explorer API of Wagtail 8.1; earlier versions' pages API). */
async function translationId(page, sourceId, locale) {
  return page.evaluate(async ([sourceId, locale]) => {
    const old = await fetch(`/admin/api/main/pages/?type=home.ArticlePage&locale=${locale}`)
    if ((old.headers.get('content-type') ?? '').includes('json')) return (await old.json()).items[0]?.id
    const explorer = async (id) => (await fetch(`/admin/api/explorer/${id}/`)).json()
    const source = await explorer(sourceId)
    const parent = await explorer(source.page.meta.parent.id)
    const home = parent.translations.find((t) => t.locale === locale)
    if (!home) return undefined
    const children = await explorer(home.id)
    return children.children.items.find((p) => p.meta.type === source.page.meta.type)?.id
  }, [sourceId, locale])
}

async function pageId(page, title) {
  // The admin API (Wagtail 8.0 and earlier) or, where it is gone (8.1), the admin's page search.
  return page.evaluate(async (t) => {
    const r = await fetch('/admin/api/main/pages/?search=' + encodeURIComponent(t))
    if ((r.headers.get('content-type') ?? '').includes('json')) {
      const id = (await r.json()).items.find((p) => p.title === t)?.id
      if (id) return id
    }
    for (const url of ['/admin/pages/search/results/?q=', '/admin/pages/search/?q=']) {
      const html = await (await fetch(url + encodeURIComponent(t))).text()
      const doc = new DOMParser().parseFromString(html, 'text/html')
      for (const a of doc.querySelectorAll('a[href*="/admin/pages/"][href$="/edit/"]')) {
        if (a.textContent.replace(/\s+/g, ' ').trim().startsWith(t)) return Number(a.getAttribute('href').match(/\/pages\/(\d+)\/edit\//)[1])
      }
    }
    return undefined
  }, title)
}

// --- Installation guide (superuser) ------------------------------------------------
{
  const { page, shot, box, go } = await session(process.env.DEMO_ADMIN_EMAIL, process.env.DEMO_ADMIN_PASSWORD)
  await go('/admin/supertext/')
  await page.getByRole('button', { name: 'Test connection' }).click()
  await page.waitForLoadState('networkidle')
  // The docs run against a local stand-in API; show the endpoint users will see.
  await page.evaluate((live) => document.querySelectorAll('code').forEach((c) => { if (c.textContent.includes('127.0.0.1')) c.textContent = live }), LIVE_API)
  // The key check's time changes with every run; show a fixed one, so the image only changes with the UI.
  await page.evaluate((time) => document.querySelectorAll('p.help').forEach((p) => { if (/^\s*Checked on /.test(p.textContent)) p.textContent = `Checked on ${time}` }), FIXED_TIME)
  // The whole page: connection, API key check, version information and the languages table.
  // Wagtail scrolls inside <main>, so make the window as tall as the page instead.
  const height = await page.evaluate(() => {
    const main = document.querySelector('main')
    return Math.ceil(main.getBoundingClientRect().top + main.scrollHeight)
  })
  await page.setViewportSize({ width: 1280, height: Math.min(height, 1500) })
  await shot('settings-supertext')
  // The Languages section alone: one drop-down per locale (Settings → Supertext → Languages).
  await shot('settings-languages', await box(page.locator('section').filter({ has: page.locator('form.supertext-languages') })))
  await page.setViewportSize({ width: 1280, height: 860 })

  await go('/admin/locales/')
  await shot('locales', { x: 0, y: 0, width: 1280, height: 420 })
}

// --- User guide (editor) ---------------------------------------------------------------
{
  const { page, shot, box, go } = await session(process.env.DEMO_EDITOR_EMAIL, process.env.DEMO_EDITOR_PASSWORD)
  await go('/admin/')
  const id = await pageId(page, 'Swiss chocolate, shipped worldwide')

  await go(`/admin/pages/${id}/edit/`)
  await page.locator('header [data-controller~="w-dropdown"] button').first().click()
  await page.getByRole('link', { name: 'Translate this page' }).waitFor()
  await shot('translate-menu', { x: 200, y: 0, width: 640, height: 420 })

  await page.getByRole('link', { name: 'Translate this page' }).click()
  await page.waitForLoadState('networkidle')
  await page.getByLabel('Select all').check()
  await shot('translate-locales', { x: 200, y: 0, width: 1000, height: 440 })
  await page.getByRole('button', { name: 'Submit' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1000)
  await shot('submitted', { x: 200, y: 0, width: 1080, height: 300 })

  // Open the German translation: the translation editor
  const germanId = await translationId(page, id, 'de-ch')
  await go(`/admin/pages/${germanId}/edit/`)
  await page.waitForTimeout(1500)
  await shot('translation-editor', { x: 200, y: 0, width: 1080, height: 860 })

  await page.getByRole('button', { name: 'Translate with Supertext' }).click()
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1500)
  await shot('translated-strings', { x: 200, y: 0, width: 1080, height: 860 })
  await page.mouse.wheel(0, 700)
  await page.waitForTimeout(800)
  await shot('translated-body', { x: 200, y: 0, width: 1080, height: 860 })

  // Publish and show the German page on the site
  await page.getByRole('button', { name: /^Publish in / }).click()
  await page.waitForLoadState('networkidle')
  // html_url can be null right after translating: Wagtail caches the site root paths (one hour, per
  // worker) and the German home page didn't exist when they were cached. The demo's URLs are
  // /<locale>/<slug>/ (i18n_patterns, the article sits right under the locale's home page).
  const germanPath = await page.evaluate(async () => {
    const r = await fetch('/admin/api/main/pages/?type=home.ArticlePage&locale=de-ch')
    if ((r.headers.get('content-type') ?? '').includes('json')) {
      const item = (await r.json()).items[0]
      return item?.meta?.html_url ? new URL(item.meta.html_url).pathname : `/de-ch/${item?.meta?.slug}/`
    }
    return undefined
  })
  // Wagtail 8.1: no pages API, and the German page often has no live link yet (Wagtail caches the
  // site root paths). Collect candidate paths (/<locale>/<slug>/) and take the first the site serves.
  const found = germanPath ? { path: germanPath } : await page.evaluate(async ([id, sourceId]) => {
    const tried = []
    const add = (p) => { if (p && /^\/de-ch\/[^/]+\/$/.test(p) && !tried.includes(p)) tried.push(p) }
    const fromSlug = (slug) => slug ? `/de-ch/${slug}/` : undefined
    const editForm = async (pid) => {
      const html = await (await fetch(`/admin/pages/${pid}/edit/`)).text()
      return { html, doc: new DOMParser().parseFromString(html, 'text/html') }
    }
    const german = await editForm(id)
    add(fromSlug(german.doc.querySelector('input[name="slug"]')?.value))
    for (const a of german.doc.querySelectorAll('a[href*="/de-ch/"]')) add(new URL(a.getAttribute('href'), location.href).pathname)
    for (const m of german.html.replace(/\\u002F|\\\//g, '/').matchAll(/\/de-ch\/[a-z0-9-]+\//g)) add(m[0])
    const r = await fetch(`/admin/api/explorer/${id}/`)
    if ((r.headers.get('content-type') ?? '').includes('json')) {
      const meta = (await r.json()).page?.meta ?? {}
      if (meta.html_url) add(new URL(meta.html_url).pathname)
      add(fromSlug(meta.slug))
    }
    // wagtail-localize keeps the source slug unless it is translated.
    add(fromSlug((await editForm(sourceId)).doc.querySelector('input[name="slug"]')?.value))
    for (const p of tried) if ((await fetch(p)).ok) return { path: p, tried }
    return { tried }
  }, [germanId, id])
  const german = found.path
  if (!german) console.error('Tried for the German page:', found.tried)
  if (!german) throw new Error('No live link of the German page in the editor')
  await page.goto(B + german)
  await page.waitForLoadState('networkidle')
  await shot('translated-page', { x: 0, y: 0, width: 1280, height: 640 })

  // Settings → Translation files: export the article as HTML for French, translate it, import it.
  await page.setViewportSize({ width: 1280, height: 1300 })
  await go('/admin/supertext/files/')
  const article = await page.locator('#supertext-source option', { hasText: 'Swiss chocolate, shipped worldwide' }).first().getAttribute('value')
  await Promise.all([page.waitForNavigation(), page.selectOption('#supertext-source', article)])
  await page.waitForLoadState('networkidle')
  await page.locator('input[name=format][value=html]').check()
  for (const target of await page.locator('input[name=targets]').all()) await target.uncheck()
  await page.locator('.supertext-files-export label', { hasText: 'fr-ch' }).locator('input').check()
  await shot('files-export', await box('#supertext-export'))
  const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('button', { name: 'Download file' }).click()])
  const exported = readFileSync(await download.path(), 'utf8')
  await page.setInputFiles('#supertext-file', { name: download.suggestedFilename(), mimeType: 'text/html', buffer: Buffer.from(translateHtml(exported, 'fr')) })
  await page.getByRole('button', { name: 'Import translation' }).click()
  await page.waitForLoadState('networkidle')
  await shot('files-import', await box('#supertext-import'))
}

await browser.close()
console.log(`Screenshots written to ${OUT}`)
