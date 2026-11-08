#!/usr/bin/env node
/**
 * Docs screenshots and CI only: answers like the Supertext AI file translation API v1 and
 * returns German, French and Italian for the demo's sample pages (samples.json: real
 * Supertext output, keyed by target language's primary subtag).
 * Unknown text comes back as "[<target>] text". Listens on :8765 (any API key).
 * Also stands in for the Supertext Commando Center: POST /report answers 202 (point
 * SUPERTEXT_REPORT_URL at http://127.0.0.1:8765/report), GET /report returns the reports so far.
 *
 * STAND_IN_DELAY=<seconds>: files stay `in_progress` that long (the demo movie shows the waiting state).
 */
import http from 'node:http'
import { randomBytes } from 'node:crypto'
import { readFileSync } from 'node:fs'

const norm = (s) => s.replace(/\s+/g, ' ').replace(/> </g, '><').trim()
const decode = (s) => s.replace(/&quot;/g, '"').replace(/&#0?39;|&apos;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
const encode = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
const samples = Object.fromEntries(
  Object.entries(JSON.parse(readFileSync(new URL('samples.json', import.meta.url), 'utf8'))).map(([lang, pairs]) => [
    lang,
    new Map(Object.entries(pairs).map(([k, v]) => [norm(k), v])),
  ]),
)
const files = new Map()
const delayMs = Number(process.env.STAND_IN_DELAY || 0) * 1000
const reports = []
// The live API's target codes (the SDK's Languages.TARGETS, October 2026) and those without formal/informal.
const TARGETS = new Set('bg cs da de-AT de-CH de-DE el en-GB en-US es fi fr-CH fr-FR gsw-u-sd-chbe gsw-u-sd-chzh hr hu it-CH it-IT ja ko nb nl pl pt-BR pt-PT rm ru sk sl sq sr-Cyrl sr-Latn sv tr zh-Hans zh-Hant'.split(' '))
const NO_POLITENESS = new Set('en-GB en-US gsw-u-sd-chbe gsw-u-sd-chzh rm sq sr-Cyrl sr-Latn'.split(' '))

http
  .createServer(async (req, res) => {
    const path = new URL(req.url, 'http://x').pathname.replace(/^\/v1/, '')
    const send = (status, body, type = 'application/json') => {
      res.writeHead(status, { 'Content-Type': type })
      res.end(type === 'application/json' ? JSON.stringify(body) : body)
    }
    if (path === '/report') {
      if (req.method === 'GET') return send(200, reports)
      const chunks = []
      for await (const chunk of req) chunks.push(chunk)
      reports.push(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'))
      console.log(`report: ${reports.at(-1).event}`)
      return send(202, { ok: true })
    }
    if (!/^Supertext-Auth-Key \S+$/.test(req.headers.authorization || '')) return send(401, { detail: 'missing key' })
    if (path === '/features') return send(200, {})
    if (req.method === 'POST') {
      const chunks = []
      for await (const chunk of req) chunks.push(chunk)
      const form = await new Request('http://x', { method: 'POST', headers: req.headers, body: Buffer.concat(chunks) }).formData()
      const file = form.get('file')
      if (!file || file.type !== 'text/html') return send(415, { detail: 'FILETYPE_NOT_ALLOWED' })
      const target = String(form.get('target_lang'))
      // Like the live API: only its exact codes ("de-CH", "es"); "de" or "es-ES" is not a pair it offers.
      if (!TARGETS.has(target)) {
        return send(404, { error_code: 'INVALID_LANGUAGE_PAIR', source_lang: form.get('source_lang'), target_lang: target })
      }
      if (form.get('politeness') && NO_POLITENESS.has(target)) {
        return send(400, { error_code: 'FEATURE_ERROR', feature: 'politeness', target_lang: target })
      }
      const id = randomBytes(6).toString('hex')
      files.set(id, { html: await file.text(), target, at: Date.now() })
      return send(200, { file_id: id })
    }
    const match = path.match(/file\/([a-f0-9]+)(\/status|\/translation)?$/)
    const file = match && files.get(match[1])
    if (!file) return send(404, {})
    if (req.method === 'DELETE') { files.delete(match[1]); return send(200, {}) }
    if (match[2] === '/status') return send(200, { status: Date.now() - file.at < delayMs ? 'in_progress' : 'done' })
    const known = samples[file.target.split('-')[0]] ?? new Map()
    const html = file.html.replace(/(<div data-st-id="\d+">)([\s\S]*?)(<\/div>\n)/g, (all, open, inner, close) => {
      const hit = known.get(norm(inner)) ?? known.get(norm(decode(inner)))
      if (hit !== undefined) return open + (known.has(norm(inner)) ? hit : encode(hit)) + close
      return open + inner.replace(/>([^<]+)</g, (m, t) => (t.trim() ? `>[${file.target}] ${t}<` : m)).replace(/^([^<]+)/, `[${file.target}] $1`) + close
    })
    send(200, html, 'text/html')
  })
  .listen(Number(process.env.PORT || 8765), '127.0.0.1', () => console.log(`Stand-in API on http://127.0.0.1:${process.env.PORT || 8765}/v1/`))
