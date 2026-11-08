#!/usr/bin/env node
/**
 * Records the install and configuration movie (a narrated MP4 that isn't committed) with the shared
 * Supertext movie kit (movie/movie-kit.mjs): the install steps from docs/INSTALLATION.md in a terminal
 * (pip install, settings, urls.py, migrate, SUPERTEXT_API_KEY, supertext_check), then, as the demo's
 * administrator: Settings → Locales, Settings → Supertext (key set, where to get a key, Test connection,
 * the Languages table) and Translate with Supertext in wagtail-localize's translation editor.
 * Run it against a demo whose translator talks to stand-in.mjs and that has a German translation of
 * the sample page (e.g. right after the demo movie). See docs/DEVELOPER.md → Demo movie.
 *
 *   BASE_URL=http://127.0.0.1:8000 DEMO_ADMIN_EMAIL=… DEMO_ADMIN_PASSWORD=… npm run movie:install
 *
 * MOVIE_OUT: where the MP4 goes (default tests/docs/movie/out/Wagtail-Supertext-Translation-install.mp4, git-ignored).
 * CHROMIUM_PATH: a Chromium to use instead of Playwright's.
 */
import { chromium } from 'playwright'
import { fileURLToPath } from 'node:url'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { MovieKit } from './movie/movie-kit.mjs'

const B = process.env.BASE_URL || 'http://127.0.0.1:8093'
const LIVE_API = 'https://api.supertext.com/v1/'
const need = (name) => process.env[name] ?? (() => { throw new Error(`Set ${name}`) })()
const here = (p) => fileURLToPath(new URL(p, import.meta.url))
const mp4 = process.env.MOVIE_OUT || here('movie/out/Wagtail-Supertext-Translation-install.mp4')
const workDir = mkdtempSync(join(tmpdir(), 'wagtail-install-movie-'))

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined })
const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1, locale: 'en-US' })
page.setDefaultTimeout(90_000)
// The movie runs against the stand-in: show the API address users see instead of the local one.
await page.addInitScript((live) => {
  const fix = (root) => {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (/127\.0\.0\.1|localhost/.test(n.textContent)) n.textContent = live
    }
  }
  new MutationObserver(() => document.body && fix(document.body)).observe(document, { subtree: true, childList: true, characterData: true })
}, LIVE_API)

// Sign in as the administrator and close Wagtail's upgrade notice (not recorded).
await page.goto(`${B}/admin/login/`)
await page.fill('#id_username', need('DEMO_ADMIN_EMAIL'))
await page.fill('#id_password', need('DEMO_ADMIN_PASSWORD'))
await page.click('button[type=submit]')
await page.waitForLoadState('networkidle')
const dismiss = page.locator('[data-w-dismissible-target], .w-dismissible button, button[aria-label*="Close"]').first()
if (await dismiss.isVisible().catch(() => false)) await dismiss.click().catch(() => {})
const germanId = await page.evaluate(async () => {
  const r = await fetch('/admin/api/main/pages/?locale=de-ch&search=Schweizer')
  return (await r.json()).items.find((p) => p.title === 'Schweizer Schokolade, weltweit versandt')?.id
})
if (!germanId) throw new Error('No German translation of the sample page: record the demo movie first')

/** Wagtail scrolls inside <main>: bring an element into view, smoothly. */
const scrollTo = async (locator, block = 'start') => {
  await locator.evaluate((el, block) => el.scrollIntoView({ block, behavior: 'smooth' }), block)
  await page.waitForTimeout(900)
}

const kit = await MovieKit.start(page, { workDir, assets: here('movie/assets'), voiceCache: here('movie/voice') })

/** Opens a page without recording the loading. */
const open = async (path, ready) => {
  kit.pause()
  await page.goto(B + path)
  await page.waitForLoadState('networkidle')
  if (ready) await ready()
  await page.waitForTimeout(1000)
  kit.resume()
}

// 1. Install (docs/INSTALLATION.md → 1. Install the package).
await kit.say('Your site needs wagtail-localize. Install the Supertext package from GitHub with pip.', {
  caption: 'Install the package with <b>pip</b>',
  during: () => kit.terminal({
    title: 'my-site',
    lines: [
      { cmd: 'pip install "wagtail-supertext-translation @ git+https://github.com/Supertext/Wagtail-Supertext-Translation@main"' },
      { out: 'Collecting wagtail-supertext-translation @ git+https://github.com/Supertext/Wagtail-Supertext-Translation@main' },
      { out: 'Building wheels for collected packages: wagtail-supertext-translation', wait: 400 },
      { out: 'Successfully installed wagtail-supertext-translation-0.1.0' },
    ],
  }),
})

// 2. Configure it (→ 2. Configure it).
await kit.say('In your Django settings, add the app, and make Supertext the machine translator of wagtail-localize.', {
  caption: 'Settings: add <b>wagtail_supertext</b> and the machine translator',
  during: () => kit.terminal({
    title: 'my-site',
    lines: [
      { cmd: 'cat mysite/settings/base.py' },
      { out: 'INSTALLED_APPS = [' },
      { out: '    "wagtail_supertext",' },
      { out: '    "wagtail_localize",' },
      { out: '    "wagtail_localize.locales",' },
      { out: '    # ...' },
      { out: ']' },
      { out: 'WAGTAILLOCALIZE_MACHINE_TRANSLATOR = {' },
      { out: '    "CLASS": "wagtail_supertext.SupertextTranslator",' },
      { out: '    "OPTIONS": {"API_KEY": os.environ.get("SUPERTEXT_API_KEY", "")},' },
      { out: '}' },
    ],
  }),
})
await kit.say('In urls.py, add the Supertext URLs before Wagtail’s own. Then run migrate.', {
  caption: 'Add the URLs, then <b>migrate</b>',
  during: () => kit.terminal({
    title: 'my-site',
    lines: [
      { cmd: 'grep supertext mysite/urls.py' },
      { out: '    path("supertext/", include("wagtail_supertext.urls")),' },
      { cmd: 'python manage.py migrate' },
      { out: '  Applying wagtail_supertext.0001_initial... OK' },
      { out: '  Applying wagtail_supertext.0002_supertextlanguage... OK' },
      { out: '  Applying wagtail_supertext.0003_supertextlanguage_review... OK' },
    ],
  }),
})

// 3. The API key (→ Getting a Supertext account and API key, 3. Check it works).
await kit.say('Set your Supertext API key as the environment variable SUPERTEXT_API_KEY. supertext_check tests it.', {
  caption: 'Set <b>SUPERTEXT_API_KEY</b>, check it with <b>supertext_check</b>',
  during: () => kit.terminal({
    title: 'my-site',
    lines: [
      { cmd: 'export SUPERTEXT_API_KEY=st-demo-0000-0000-0000' },
      { cmd: 'python manage.py supertext_check' },
      { out: `API: ${LIVE_API}`, wait: 400 },
      { out: 'API key: set (environment)' },
      { out: 'API key check: ok (HTTP 200). Connected. The API key works.' },
    ],
  }),
})

// 4. Wagtail's own locales.
await open('/admin/locales/', () => page.getByText('Deutsch (Schweiz)').first().waitFor())
await kit.say("Supertext translates into Wagtail's locales. Set them up first, under Settings, Locales.", {
  caption: '<b>Settings → Locales</b>: your site’s languages',
})

// 5. Settings → Supertext.
await kit.say('Restart Wagtail, and open Settings, Supertext. The API key is set.', {
  caption: 'Open <b>Settings → Supertext</b>',
  during: async () => {
    await kit.click(page.getByRole('button', { name: 'Settings' }))
    await page.waitForTimeout(500)
    await kit.click(page.getByRole('link', { name: 'Supertext', exact: true }))
    await page.waitForLoadState('networkidle')
    await kit.moveTo(page.getByText('Set (SUPERTEXT_API_KEY environment variable)'))
  },
})
await kit.say('Generate your API key at supertext.com, Integrations, API. No account yet? Create one at supertext.com.', {
  caption: 'Generate your API key at <b>supertext.com → Integrations → API</b>. No account yet? Create one at supertext.com.',
  during: () => kit.moveTo(page.getByRole('link', { name: /Integrations/ }).first()),
})
await kit.say('Click Test connection. The connection to Supertext works.', {
  caption: '<b>Test connection</b>: the API key works',
  during: async () => {
    await kit.click(page.getByRole('button', { name: 'Test connection' }))
    await page.waitForLoadState('networkidle')
    await kit.moveTo(page.locator('main').getByText('API key checked: the connection to Supertext works.').last())
  },
})

// 6. Languages.
const deRow = page.locator('form.supertext-languages tr').filter({ hasText: 'de-ch' })
await kit.say('Under Languages, each locale gets a Supertext language. For German, Swiss German is already suggested.', {
  caption: '<b>Languages</b>: the Supertext language for each locale',
  during: async () => {
    await scrollTo(page.locator('form.supertext-languages'))
    const select = deRow.locator('select').first()
    await kit.moveTo(select)
    await select.selectOption('de-CH')
    await kit.hold(600)
  },
})
await kit.say('Translation review can be ticked for each language. Leave it off for now.', {
  caption: '<b>Translation review</b>: optional, per language',
  during: () => kit.moveTo(deRow.locator('input[type=checkbox]').first()),
})
await kit.say('Click Save languages.', {
  caption: 'Click <b>Save languages</b>',
  during: async () => {
    const save = page.getByRole('button', { name: 'Save languages' })
    await scrollTo(save, 'center')
    await kit.click(save, { noWaitAfter: true })
  },
})
await kit.say('The languages are saved.', {
  caption: 'The languages are saved',
  during: () => page.getByText('The languages are saved.').waitFor(),
})

// 7. Where editors find it.
await open(`/admin/pages/${germanId}/edit/`, () => page.getByRole('button', { name: 'Translate with Supertext' }).waitFor())
await kit.say("Done. Editors translate a page with wagtail-localize, and find Translate with Supertext in the translation editor.", {
  caption: 'Editors find <b>Translate with Supertext</b> in the translation',
  during: () => kit.moveTo(page.getByRole('button', { name: 'Translate with Supertext' })),
})
await kit.caption('')

await kit.finish(mp4, { system: 'Wagtail', repo: 'Wagtail-Supertext-Translation', kind: 'install' })
await browser.close()
rmSync(workDir, { recursive: true, force: true })
console.log(`Saved ${mp4}`)
