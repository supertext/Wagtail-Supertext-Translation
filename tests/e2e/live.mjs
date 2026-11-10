#!/usr/bin/env node
/**
 * End-to-end test against the REAL Supertext API: a fresh demo (nothing translated yet)
 * with a real SUPERTEXT_API_KEY. The editor (DEMO_EDITOR_*) works through Wagtail's own
 * admin like in docs/USER_GUIDE.md: "Translate this page" for every English page into every
 * other locale, then "Translate with Supertext" and "Publish in …" in wagtail-localize's
 * translation editor. Then the database is checked: every English page has a translated
 * copy per locale, the Supertext step itself publishes nothing, every string got a
 * translation, the copies are really in the target language (de-CH without "ß"),
 * bold, italics, lists and links survived, and translating again neither duplicates nor
 * changes anything (wagtail-localize only passes strings without a translation).
 * See "End-to-end tests" in docs/DEVELOPER.md.
 *
 *   BASE_URL=http://127.0.0.1:8081 \
 *   DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/wagtail_live \
 *   EDITOR_EMAIL=… EDITOR_PASSWORD=… node tests/e2e/live.mjs
 */
import { chromium } from 'playwright';
import pg from 'pg';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const B = (process.env.BASE_URL ?? 'http://127.0.0.1:8081').replace(/\/$/, '');
const need = (name) => process.env[name] || (() => { throw new Error(`Set ${name}`); })();
const artifacts = fileURLToPath(new URL('./artifacts/', import.meta.url));
mkdirSync(artifacts, { recursive: true });
const TRANSLATE_TIMEOUT = 600_000; // one page into one language, inside the editor's request

const failures = [];
function check(name, ok, detail = '') {
	console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
	if (!ok) failures.push(`${name}${detail ? ` — ${detail}` : ''}`);
	return ok;
}
const note = (text) => console.log(`note ${text}`);

// Common words, to tell the target languages apart. Lower case, whole words.
const STOPWORDS = {
	en: ['the', 'and', 'is', 'are', 'with', 'for', 'you', 'your', 'our', 'of', 'to', 'we', 'who', 'what'],
	de: ['der', 'die', 'das', 'und', 'ist', 'sind', 'mit', 'für', 'sie', 'ihre', 'ihr', 'unser', 'unsere', 'wir', 'eine', 'ein', 'den', 'zu', 'von', 'was'],
	fr: ['le', 'les', 'et', 'est', 'sont', 'avec', 'pour', 'vous', 'votre', 'vos', 'notre', 'nos', 'nous', 'des', 'une', 'un', 'du', 'de', 'qui', 'que'],
	it: ['il', 'lo', 'gli', 'e', 'è', 'sono', 'con', 'per', 'lei', 'sua', 'suo', 'nostro', 'nostra', 'noi', 'una', 'un', 'del', 'della', 'di', 'che', 'chi'],
};
const words = (text) => text.toLowerCase().match(/[\p{L}]+/gu) ?? [];
const score = (text, lang) => words(text).filter((w) => STOPWORDS[lang].includes(w)).length;
const plain = (v) => String(v ?? '').replace(/<[^>]+>/g, ' ');
const count = (haystack, needle) => String(haystack ?? '').split(needle).length - 1;

const LEFTOVERS = ['data-st-id', 'data-st-ph', 'st-ph-', '<div'];
// Markup the sample content has, counted per field. Links must keep their target (wagtail-localize
// sends <a id="a1"> and puts the href back), internal links (linktype="page") their page.
const MARKUP = [['bold', '<b>'], ['italics', '<i>'], ['list items', '<li>'], ['links', '<a href="'], ['internal links', 'linktype="page"'], ['wagtail-localize link ids', '<a id=']];

const db = new pg.Client({ connectionString: need('DATABASE_URL') });
await db.connect();
const q = async (sql, params = []) => (await db.query(sql, params)).rows;

/** Page rows with their locale and model (home_homepage, home_articlepage). */
const PAGE = `SELECT p.id, p.title, p.slug, p.path, p.translation_key, p.live, p.live_revision_id, p.last_published_at,
	l.language_code, ct.app_label || '_' || ct.model AS model
	FROM wagtailcore_page p JOIN wagtailcore_locale l ON l.id = p.locale_id
	JOIN django_content_type ct ON ct.id = p.content_type_id`;

/** The translatable fields of a page: title, slug, rich-text intro and the StreamField blocks. */
async function fields(page) {
	const out = { title: page.title };
	if (/^home_(homepage|articlepage)$/.test(page.model)) {
		const [row] = await q(`SELECT intro, body FROM ${page.model} WHERE page_ptr_id = $1`, [page.id]);
		if (row?.intro) out.intro = row.intro;
		const body = typeof row?.body === 'string' ? JSON.parse(row.body || '[]') : row?.body ?? [];
		body.forEach((block, i) => {
			if (typeof block.value === 'string') out[`body[${i}].${block.type}`] = block.value;
			else for (const [k, v] of Object.entries(block.value ?? {})) if (typeof v === 'string' && v) out[`body[${i}].${block.type}.${k}`] = v;
		});
	}
	return out;
}

// --- Setup -------------------------------------------------------------------------------
const sourcePages = await q(`${PAGE} WHERE l.language_code = 'en' AND p.depth > 1 ORDER BY p.path`);
const locales = (await q("SELECT language_code FROM wagtailcore_locale WHERE language_code <> 'en' ORDER BY language_code")).map((r) => r.language_code);
console.log(`English pages: ${sourcePages.map((p) => `"${p.title}"`).join(', ')}; target locales: ${locales.join(', ')}`);
check('demo has English pages and target locales', sourcePages.length > 0 && locales.length > 0);
check('fresh demo (nothing translated yet)', (await q(`${PAGE} WHERE l.language_code <> 'en' AND p.depth > 1`)).length === 0);
const sourceFields = new Map();
for (const p of sourcePages) sourceFields.set(p.translation_key, await fields(p));

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, locale: 'en-US' });
page.setDefaultTimeout(30_000);
let shotNo = 0;
const shot = (name) => page.screenshot({ path: `${artifacts}${String(++shotNo).padStart(2, '0')}-${name}.png`, fullPage: true }).catch(() => {});

/** Wagtail's admin messages on the current page, as { kind, text }. */
async function messages() {
	return page.locator('.messages li').evaluateAll((items) => items.map((li) => ({
		kind: /error/.test(li.className) ? 'error' : /warning/.test(li.className) ? 'warning' : 'info',
		text: li.textContent.replace(/\s+/g, ' ').trim(),
	})));
}
const translationOf = async (sourcePage, locale) => (await q(`${PAGE} WHERE p.translation_key = $1 AND l.language_code = $2`, [sourcePage.translation_key, locale]))[0];

// --- Log in as the editor ----------------------------------------------------------------
await page.goto(`${B}/admin/login/`);
await page.fill('#id_username', need('EDITOR_EMAIL'));
await page.fill('#id_password', need('EDITOR_PASSWORD'));
await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
if (!check('editor can log in (DEMO_EDITOR_*)', !page.url().includes('/admin/login'))) {
	await shot('login');
	throw new Error(failures.join('\n'));
}

// --- "Translate this page" into every locale (wagtail-localize) ---------------------------
for (const source of sourcePages) {
	await page.goto(`${B}/admin/pages/${source.id}/edit/`);
	await page.locator('header [data-controller~="w-dropdown"] button').first().click();
	await page.getByRole('link', { name: 'Translate this page' }).click();
	await page.waitForLoadState('networkidle');
	await page.getByLabel('Select all').check();
	await Promise.all([page.waitForNavigation(), page.getByRole('button', { name: 'Submit' }).click()]);
	const msgs = await messages();
	check(`"${source.title}": submitted for translation`, msgs.some((m) => /submitted for translation/i.test(m.text)) && !msgs.some((m) => m.kind === 'error'), msgs.map((m) => m.text).join(' | '));
}

// --- "Translate with Supertext", then "Publish in …" --------------------------------------
async function translateWithSupertext(target, label) {
	await page.goto(`${B}/admin/pages/${target.id}/edit/`);
	const button = page.getByRole('button', { name: 'Translate with Supertext' });
	await button.waitFor();
	const started = Date.now();
	await Promise.all([
		page.waitForResponse((r) => r.request().method() === 'POST' && r.url().includes('/machine_translate/'), { timeout: TRANSLATE_TIMEOUT }),
		button.click(),
	]);
	await page.waitForLoadState('networkidle');
	await shot(`${label}-${target.language_code}-${target.slug}`);
	console.log(`${label}: "${target.title}" (${target.language_code}) ${Math.round((Date.now() - started) / 1000)} s`);
	return messages();
}

const firstRun = {};
for (const locale of locales) {
	firstRun[locale] = [];
	for (const source of sourcePages) {
		const before = await translationOf(source, locale);
		const tag = `"${source.title}" (${locale})`;
		if (!check(`${tag}: translated copy created by wagtail-localize`, !!before)) continue;
		const msgs = await translateWithSupertext(before, 'first-run');
		check(`${tag}: "Successfully translated with Supertext."`, msgs.some((m) => /Successfully translated with Supertext/.test(m.text)), msgs.map((m) => m.text).join(' | '));
		check(`${tag}: no error message`, !msgs.some((m) => m.kind === 'error'), msgs.filter((m) => m.kind === 'error').map((m) => m.text).join(' | '));

		// The Supertext step only fills in strings; publishing is the editor's decision.
		const after = await translationOf(source, locale);
		check(`${tag}: translating published nothing`, after.live === before.live && String(after.live_revision_id) === String(before.live_revision_id) && String(after.last_published_at) === String(before.last_published_at));

		// The editor reviews and publishes.
		await Promise.all([
			page.waitForResponse((r) => r.request().method() === 'POST' && r.url().includes(`/pages/${before.id}/edit/`), { timeout: 120_000 }),
			page.getByRole('button', { name: /^Publish in / }).click(),
		]);
		await page.waitForLoadState('networkidle');
		const published = await messages();
		check(`${tag}: published without missing translations`, published.some((m) => /^Published /.test(m.text) && m.kind !== 'warning') && !published.some((m) => m.kind === 'error'), published.map((m) => m.text).join(' | '));
		firstRun[locale].push(await translationOf(source, locale));
	}
}

// --- Database checks ----------------------------------------------------------------------
for (const locale of locales) {
	const lang = locale.split('-')[0];
	const copies = (await q(`${PAGE} WHERE l.language_code = $1 AND p.depth > 1`, [locale]));
	check(`${locale}: one copy per English page`, copies.length === sourcePages.length && sourcePages.every((s) => copies.some((c) => c.translation_key === s.translation_key)), `${copies.length}/${sourcePages.length}`);

	let total = 0;
	let changed = 0;
	let text = '';
	for (const source of sourcePages) {
		const copy = copies.find((c) => c.translation_key === source.translation_key);
		if (!copy) continue;
		const tag = `"${source.title}" (${locale})`;
		const src = sourceFields.get(source.translation_key);
		const out = await fields(copy);
		for (const [name, value] of Object.entries(src)) {
			if (!plain(value).trim()) continue;
			total++;
			const v = String(out[name] ?? '');
			if (!check(`${tag}: ${name} filled`, plain(v).trim() !== '')) continue;
			// A single word or a name can stay the same ("Supertext Wagtail Demo"), so count, don't fail.
			if (v !== value) changed++;
			else note(`${tag}: ${name} unchanged: ${JSON.stringify(plain(v).trim())}`);
			text += ` ${plain(v)}`;
			const left = LEFTOVERS.filter((l) => v.includes(l));
			check(`${tag}: ${name} has no protocol leftovers`, left.length === 0, left.join(', '));
			for (const [what, needle] of MARKUP) {
				if (count(value, needle) || count(v, needle)) check(`${tag}: ${name} keeps its ${what}`, count(v, needle) === count(value, needle), `${count(v, needle)}/${count(value, needle)}`);
			}
			for (const href of String(value).match(/href="[^"]*"/g) ?? []) check(`${tag}: ${name} keeps the link ${href}`, v.includes(href));
		}
		if (source.slug.split('-').length > 3) check(`${tag}: slug translated`, copy.slug !== source.slug, copy.slug);
		else note(`${tag}: slug ${copy.slug} (source ${source.slug})`);
		check(`${tag}: published by the editor`, copy.live === true);
	}
	check(`${locale}: texts were changed by the translation`, total > 0 && changed / total >= 0.8, `${changed}/${total} fields`);
	if (STOPWORDS[lang]) {
		const scores = Object.fromEntries(Object.keys(STOPWORDS).map((l) => [l, score(text, l)]));
		const best = Object.entries(scores).sort((a, b) => b[1] - a[1])[0][0];
		check(`${locale}: text is in the target language`, best === lang, JSON.stringify(scores));
	}
	if (/^de-ch$/i.test(locale)) check(`${locale}: Swiss German spelling (no "ß")`, !text.includes('ß'));
}

// --- Translating again: same records, nothing overwritten -----------------------------------
// wagtail-localize passes only strings without a translation, so a second "Translate with
// Supertext" has nothing to do; it must not create pages or change the published ones.
const pagesBefore = (await q(`${PAGE} WHERE p.depth > 1 ORDER BY p.id`)).map((p) => `${p.id}:${p.live_revision_id}`).join(',');
for (const locale of locales) {
	for (const target of firstRun[locale]) {
		const tag = `"${target.title}" (${locale})`;
		await page.goto(`${B}/admin/pages/${target.id}/edit/`);
		const button = page.getByRole('button', { name: 'Translate with Supertext' });
		await button.waitFor();
		if (await button.isDisabled()) {
			// The translation editor disables the button when every string has a translation.
			check(`${tag}: "Translate with Supertext" disabled, nothing left to translate`, true);
			const msgs = await messages();
			check(`${tag}: translation editor shows no error`, !msgs.some((m) => m.kind === 'error'), msgs.map((m) => m.text).join(' | '));
			continue;
		}
		const msgs = await translateWithSupertext(target, 'second-run');
		check(`${tag}: translating again shows no error`, !msgs.some((m) => m.kind === 'error'), msgs.map((m) => m.text).join(' | '));
		if (msgs.some((m) => /anything left to translate/.test(m.text))) note(`${tag}: "There isn't anything left to translate."`);
	}
}
const pagesAfter = (await q(`${PAGE} WHERE p.depth > 1 ORDER BY p.id`)).map((p) => `${p.id}:${p.live_revision_id}`).join(',');
check('translating again updates in place (no new pages, published revisions unchanged)', pagesBefore === pagesAfter);

await browser.close();
await db.end();

if (failures.length) {
	console.error(`\n${failures.length} check(s) failed:\n- ${failures.join('\n- ')}`);
	process.exit(1);
}
console.log('\nAll end-to-end checks passed.');
