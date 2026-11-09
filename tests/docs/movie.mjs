#!/usr/bin/env node
/**
 * Records the README's demo movie (docs/images/demo.gif, plus a narrated MP4 that isn't committed)
 * with the shared Supertext movie kit (movie/movie-kit.mjs), as the demo's editor: the sample page
 * in English, wagtail-localize's "Translate this page" into German, "Translate with Supertext" in
 * the translation editor, the German texts and "Publish". Run it against a fresh demo (the page must
 * have no German translation yet) whose translator talks to stand-in.mjs. See docs/DEVELOPER.md → Demo movie.
 *
 *   BASE_URL=http://127.0.0.1:8000 DEMO_EDITOR_EMAIL=… DEMO_EDITOR_PASSWORD=… npm run movie
 *
 * MOVIE_OUT: where the MP4 goes (default tests/docs/movie/out/Wagtail-Supertext-Translation.mp4, git-ignored).
 * CHROMIUM_PATH: a Chromium to use instead of Playwright's.
 */
import { chromium } from 'playwright'
import { fileURLToPath } from 'node:url'
import { mkdtempSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { MovieKit } from './movie/movie-kit.mjs'

const B = process.env.BASE_URL || 'http://127.0.0.1:8093'
const need = (name) => process.env[name] ?? (() => { throw new Error(`Set ${name}`) })()
const here = (p) => fileURLToPath(new URL(p, import.meta.url))
const mp4 = process.env.MOVIE_OUT || here('movie/out/Wagtail-Supertext-Translation.mp4')
const gif = here('../../docs/images/demo.gif')
const workDir = mkdtempSync(join(tmpdir(), 'wagtail-movie-'))

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined })
const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1, locale: 'en-US' })
page.setDefaultTimeout(90_000)

// Sign in as the editor and open the sample page (not recorded).
await page.goto(`${B}/admin/login/`)
await page.fill('#id_username', need('DEMO_EDITOR_EMAIL'))
await page.fill('#id_password', need('DEMO_EDITOR_PASSWORD'))
await page.click('button[type=submit]')
await page.waitForLoadState('networkidle')
const id = await page.evaluate(async () => {
  const r = await fetch('/admin/api/main/pages/?search=' + encodeURIComponent('Swiss chocolate, shipped worldwide'))
  return (await r.json()).items.find((p) => p.title === 'Swiss chocolate, shipped worldwide')?.id
})
if (!id) throw new Error('Sample page not found: run demo_setup first')
await page.goto(`${B}/admin/pages/${id}/edit/`)
await page.waitForLoadState('networkidle')
await page.waitForTimeout(800)

/** Wagtail scrolls inside <main>: bring an element into view, smoothly. */
const scrollTo = async (locator, block = 'start') => {
  await locator.evaluate((el, block) => el.scrollIntoView({ block, behavior: 'smooth' }), block)
  await page.waitForTimeout(900)
}

const kit = await MovieKit.start(page, { workDir, assets: here('movie/assets'), voiceCache: here('movie/voice') })

// 1. The page in English.
await kit.say("Here's a page in Wagtail, written in English.", {
  caption: 'A page in English',
  during: async () => {
    await kit.hold(600)
    await scrollTo(page.getByText('From Bern to the world').first(), 'center')
    await kit.hold(500)
    await scrollTo(page.locator('h1, [name=title]').first(), 'center')
  },
})

// 2. wagtail-localize: Translate this page → German → Submit.
await kit.say('To translate it, open the page menu and choose Translate this page.', {
  caption: 'Page menu → <b>Translate this page</b>',
  during: async () => {
    await kit.click(page.locator('header [data-controller~="w-dropdown"] button').first())
    await page.waitForTimeout(300)
    await kit.click(page.getByRole('link', { name: 'Translate this page' }))
    await page.waitForLoadState('networkidle')
  },
})
await kit.say('Tick German, then click Submit.', {
  caption: 'Tick <b>Deutsch (Schweiz)</b>, then <b>Submit</b>',
  during: async () => {
    await kit.click(page.getByLabel('Deutsch (Schweiz)'))
    await kit.click(page.getByRole('button', { name: 'Submit' }))
    await page.waitForLoadState('networkidle')
  },
})

// 3. The translation editor: Translate with Supertext.
const translate = page.getByRole('button', { name: 'Translate with Supertext' })
await translate.waitFor()
await kit.say('wagtail-localize opens the German translation. Click Translate with Supertext.', {
  caption: 'In the translation, click <b>Translate with Supertext</b>',
  // The translation runs inside this request: don't let Playwright wait for it here.
  during: () => kit.click(translate, { noWaitAfter: true }),
})

// 4. Waiting (cut).
await kit.say('Supertext translates the content.', { caption: 'Supertext translates the content…', during: () => kit.hold(1200) })
kit.pause()
await page.getByText('Successfully translated with Supertext.').waitFor({ timeout: 180_000 })
await page.waitForLoadState('networkidle')
await page.waitForTimeout(600)
kit.resume()

// 5. The German texts.
await kit.say('A few seconds later, the German texts are in place.', {
  caption: 'The German texts are in place',
  during: async () => {
    await kit.hold(900)
    await scrollTo(page.getByText('Schweizer Schokolade, weltweit versandt').last(), 'center')
  },
})
await kit.say('Review them, just like your own text.', {
  caption: 'Review the German version',
  during: async () => {
    await kit.hold(300)
    await page.mouse.move(700, 400)
    await page.mouse.wheel(0, 650)
    await kit.hold(1200)
  },
})

// 6. Publish.
// Short caption: the Publish button sits at the bottom, next to the caption bar.
await kit.say('Then click Publish.', {
  caption: 'Click <b>Publish</b>',
  during: async () => {
    await kit.click(page.getByRole('button', { name: /^Publish in / }))
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(800)
  },
})
await kit.say('Done. The page is now live in German.', { caption: 'Published: the page is live in German' })
await kit.caption('')

await kit.finish(mp4, { system: 'Wagtail', repo: 'Wagtail-Supertext-Translation', gif })
await browser.close()
rmSync(workDir, { recursive: true, force: true })
console.log(`Saved ${mp4} and ${gif} (${(statSync(gif).size / 1e6).toFixed(2)} MB)`)
