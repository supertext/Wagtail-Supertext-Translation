# Installation guide — Supertext Translation for Wagtail

For administrators and developers adding Supertext to a Wagtail site.

## Just want to try it?

`demo/` is a ready-to-run Wagtail 8 site with English sample content, German, French and Italian (Switzerland), wagtail-localize and this package. It is also what runs the public Supertext demo. See the *Demo* section of the [developer guide](DEVELOPER.md#demo-railway).

## Requirements

| | |
| --- | --- |
| Wagtail | 8.1 (tested), 7.0–8.0 (tested in CI) |
| wagtail-localize | 1.12 or newer |
| Python | 3.10 or newer (whatever your Wagtail needs; tested up to 3.14) |
| Supertext | An account ([create one or log in](https://www.supertext.com/person/en/account/signin)) and an API key ([supertext.com → Integrations → API](https://www.supertext.com/en/integrations/api), needs the Admin role). See [Getting a Supertext account and API key](#getting-a-supertext-account-and-api-key). |
| Network | The server must reach `https://api.supertext.com` over HTTPS (and, for [Translation review](#translation-review), Supertext's review service) |

This package is a machine translator **for wagtail-localize**. If your site doesn't use wagtail-localize yet, set it up first: [wagtail-localize installation](https://wagtail-localize.org/stable/how-to/installation/). That includes `WAGTAIL_I18N_ENABLED = True`, `WAGTAIL_CONTENT_LANGUAGES` and making your page models translatable (Wagtail pages are by default).

## 1. Install the package

The package isn't on PyPI yet. Install it from GitHub:

```bash
pip install "wagtail-supertext-translation @ git+https://github.com/Supertext/Wagtail-Supertext-Translation@main"
```

## 2. Configure it

In your Django settings:

```python
import os

INSTALLED_APPS = [
    # ...
    "wagtail_supertext",          # adds Settings → Supertext and the supertext_check command
    "wagtail_localize",
    "wagtail_localize.locales",
    # ...
]

WAGTAILLOCALIZE_MACHINE_TRANSLATOR = {
    "CLASS": "wagtail_supertext.SupertextTranslator",
    "OPTIONS": {
        "API_KEY": os.environ.get("SUPERTEXT_API_KEY", ""),
    },
}
```

wagtail-localize uses one machine translator per site; this replaces DeepL or Google if you had one configured.

In your `urls.py`, add the plugin's public ping endpoint **before** Wagtail's catch-all pattern (it lets Supertext see that the site is reachable, see [Version information and the API key check](#version-information-and-the-api-key-check)):

```python
urlpatterns = [
    path("admin/", include(wagtailadmin_urls)),
    path("supertext/", include("wagtail_supertext.urls")),   # GET /supertext/ping, no login
    # ...
    path("", include(wagtail_urls)),
]
```

Then create the plugin's table (it keeps the latest API key check and what was last reported):

```bash
python manage.py migrate
```

### Getting a Supertext account and API key

1. **No Supertext account yet?** [Create one at supertext.com](https://www.supertext.com/person/en/account/signin) (the same page logs you in if you already have one).
2. **Generate your API key** at [supertext.com → Integrations → API](https://www.supertext.com/en/integrations/api). This requires the **Admin** role in your Supertext account; ask your Supertext account admin if you don't see the page.

**API key:** set the environment variable **`SUPERTEXT_API_KEY`** on the server. It always takes precedence over `API_KEY`, and keeps the key out of your code. You can use the key exactly as Supertext shows it, with the leading `Supertext-Auth-Key`, or without it.

## 3. Check it works

As a superuser, open **Settings → Supertext** in the Wagtail admin and click **Test connection**. *API key checked: the connection to Supertext works.* means everything is in place. The page also shows the installed plugin version (linked to its release notes on GitHub), the latest API key check with its time, whether version information is sent to Supertext (with the ping endpoint's address), and the **Languages** table, where you choose the Supertext language and tone of each locale (see [Locales](#4-locales)).

![Settings → Supertext: API key set from the SUPERTEXT_API_KEY environment variable, API https://api.supertext.com/v1/, Plugin version 0.1.0, API key check "API key checked: the connection to Supertext works." with its time, the Test connection button, Send version information to Supertext: On with the ping endpoint, and the Languages table with a drop-down per locale: Deutsch (Schweiz) German (Switzerland) (de-CH) formal, English (source language) English (United States) (en-US), Français (Suisse) French (Switzerland) (fr-CH) formal, Italiano (Svizzera) Italian (Switzerland) (it-CH) formal, and Save languages](images/settings-supertext.png)

From the command line (checks the key, prints the result and sends a `check` version report if reports are on; exits with an error when Supertext doesn't accept the key):

```bash
python manage.py supertext_check
```

## 4. Locales

Supertext translates into the site's **locales** (*Settings → Locales*, from `WAGTAIL_CONTENT_LANGUAGES`). Each locale needs a Supertext language. Supertext accepts exactly these target codes (from its `features` list, October 2026):

| Language | Supertext codes |
| --- | --- |
| German, English, French, Italian, Portuguese | `de-DE`, `de-CH`, `de-AT`, `en-US`, `en-GB`, `fr-FR`, `fr-CH`, `it-IT`, `it-CH`, `pt-PT`, `pt-BR` |
| Chinese, Serbian | `zh-Hans`, `zh-Hant`, `sr-Latn`, `sr-Cyrl` |
| Swiss German | `gsw-u-sd-chzh` (Zurich), `gsw-u-sd-chbe` (Bern) |
| All others, without region | `bg`, `cs`, `da`, `el`, `es`, `fi`, `hr`, `hu`, `ja`, `ko`, `nb`, `nl`, `pl`, `rm`, `ru`, `sk`, `sl`, `sq`, `sv`, `tr` |

### Choosing the Supertext language: *Settings → Supertext → Languages*

*Settings → Supertext* (superusers) has a **Languages** table with one row per locale, the source language included (one choice serves both directions). Each row has a drop-down with Supertext's target languages (*German (Switzerland) (de-CH)*, *Spanish (es)*, …) and, where the chosen language offers it, a **Tone** drop-down (*Default*, *Formal (Sie, vous)*, *Informal (du, tu)*), and a **Translation review** checkbox (off by default, see [Translation review](#translation-review)). **Save languages** stores the choices in the database (table `wagtail_supertext_supertextlanguage`, created by `migrate`); only codes from Supertext's list are accepted.

![Settings → Supertext → Languages: one row per locale with a drop-down of Supertext languages, a Tone drop-down and a Translation review checkbox (not ticked), and the Save languages button](images/settings-languages.png)

The drop-down preselects the saved choice, else the **suggested** language. When translating, a locale's Supertext language is:

1. the `code` in the `LANGUAGES` option, if set (see below; it wins, and the row shows *Set in the LANGUAGES option* instead of a drop-down);
2. else the language chosen and saved on *Settings → Supertext*;
3. else the locale's own code, if it is a Supertext code (apart from case and `_`/`-`): `de-ch` → `de-CH`, `fr-ch` → `fr-CH`, `es` → `es`;
4. else the default, if there is an obvious one:

   | Wagtail locale | Default |
   | --- | --- |
   | `de`, `en`, `fr`, `it`, `pt` | `de-DE`, `en-US`, `fr-FR`, `it-IT`, `pt-PT` |
   | `zh`, `zh-cn`, `zh-sg` | `zh-Hans` |
   | `zh-tw`, `zh-hk`, `zh-mo` | `zh-Hant` |
   | `sr` | `sr-Latn` |
   | a region of a language Supertext offers without region | the bare language: `es-es` → `es`, `nl-be` → `nl`, `sl-si` → `sl` |

5. else nothing: the row shows **— choose —** and *No Supertext language: choose one, or this locale can't be translated*, the editor gets *The language … has no Supertext language yet*, and nothing is sent to Supertext. This is the case for locales without an obvious default, e.g. `en-ie`, `de-li`, `gsw`, `sr-rs`.

- The source language is sent without region (`en-gb` → `en`; a chosen or mapped source uses the language of its choice: `en` set to `en-US` translates into `en-US` and from `en`).
- Translating within one language (`en` → `en-GB`) isn't offered.

![Settings → Locales: Deutsch (Schweiz), English, Français (Suisse) and Italiano (Svizzera)](images/locales.png)

### Override in the settings: `LANGUAGES`

For automated setups, the `LANGUAGES` option sets the language, tone and *Translation review* per locale (keyed by its Wagtail language code) in the Django settings. It wins over *Settings → Supertext*, per entry: a locale with `code` here shows that code instead of a drop-down, one with `politeness` here shows that tone, one with `review` (`True`/`False`) shows *On*/*Off* instead of the checkbox.

```python
"OPTIONS": {
    "API_KEY": os.environ.get("SUPERTEXT_API_KEY", ""),
    "LANGUAGES": {
        "fr": {"code": "fr-CH", "politeness": "more"},  # Swiss French, formal (vous)
        "de": {"code": "de-CH", "politeness": "less"},  # Swiss German, informal (du)
        "zh-cn": {"code": "zh-Hans"},                    # Chinese (Simplified)
        "it": {"code": "it-CH", "review": True},         # Swiss Italian, with Translation review
    },
},
```

Sites that already had `LANGUAGES` keep working unchanged after the update: their entries still win. To manage a language on the settings page instead, remove its entry and restart.

`politeness`: `more` = formal (*Sie, vous*), `less` = informal (*du, tu*), otherwise Supertext's default. Supertext offers no formal/informal choice for English, Swiss German, Romansh, Albanian and Serbian: the settings page hides the tone for those, and in `LANGUAGES` leave `politeness` out for them. If it is set anyway, the plugin doesn't drop it silently: the editor gets *Supertext offers no formal or informal tone for this language*, nothing is sent, and *Settings → Supertext* shows a warning next to the tone.

## Translation review

With **Translation review**, people check the translations on the website itself and correct them there: customers or Supertext linguists open the original and the translated page side by side in Chrome with the *Supertext Localization Review* extension and fix texts in a side panel. Their corrections come back into Wagtail.

Turn it on per language for the locale that is translated **into**: *Settings → Supertext → Languages* → tick **Translation review** in that locale's row → **Save languages** (or `"review": True` for that locale in the `LANGUAGES` option). Untick it to turn it off again; nothing else changes.

- Translations into that locale then go through Supertext's review service instead of directly to Supertext. It answers exactly like Supertext and passes everything on with your API key, so billing, languages, formal/informal and error messages stay the same.
- The review service keeps every translation of your account. A text it has translated before gets the same translation again (without a new Supertext call), and once a reviewer has corrected it, the corrected text.
- **Corrections arrive with the next translation:** wagtail-localize's *Translate with Supertext* only fills texts that have no translation yet (it never overwrites one), so a reviewer's correction reaches Wagtail when that text is translated again: clear its translation in the translation editor (**Edit** → delete the text → save) and click **Translate with Supertext**. New and changed texts that were corrected before get the corrected wording right away.
- Reviewers get a review link for your site from Supertext; nothing to set up in Wagtail.
- The review service works with keys of the **live** Supertext API. With `ENVIRONMENT` `"staging"` or `"testing"` (or an `ENDPOINT`), leave review off.
- *Export for translation* and *Import translation* don't use the review service. The API key check (*Test connection*, `manage.py supertext_check`) still goes directly to Supertext.
- `REVIEW_URL` (default: Supertext's review service) sets its address; `""` turns review off for every locale (the checkboxes then show –). Only change it when Supertext gives you another address.

## Version information and the API key check

**Send version information to Supertext** is on by default. The plugin then sends the site URL and the versions of this plugin, Wagtail and Python to Supertext after an update and when the API key changes, so Supertext support can see which version you run. A report contains exactly: the site URL (`WAGTAILADMIN_BASE_URL`, else the address the admin request came in on, else the default site's root URL), the plugin's name (`Wagtail-Supertext-Translation`) and version, the Wagtail and Python versions, the Supertext SDK version, whether an API key is set, the result of the API key check below (status, time, HTTP status, Supertext's error code) and the address of the ping endpoint. **No content, no API key and no personal data are sent.** Reports go from the Wagtail server, never from the browser, in the background: a report that fails is not retried for an hour and never slows down or breaks a translation.

Turn it off with `"VERSION_REPORTS": False` in the `OPTIONS`, or the environment variable `SUPERTEXT_VERSION_REPORTS=0` (it wins over the option; `1` turns it on). Nothing is sent at all then, and the ping endpoint answers 404. *Settings → Supertext* shows whether it is on and where that comes from.

**The API key check** runs whether or not version information is sent: when the plugin first runs with a key it hasn't checked yet (on *Settings → Supertext* or a translation; the key is compared by a SHA-256 fingerprint kept in the database, never the key itself), on **Test connection**, with `manage.py supertext_check`, and when Supertext rejects the key during a translation. It's Supertext's cost-free `features` call; nothing is translated or billed. *Settings → Supertext* shows the latest result with its time: *the connection works*, *Supertext rejected the API key* (with the links to create an account and generate a key), *no API key is set*, *Supertext can't be reached* or *Supertext answered with an error*.

**The ping endpoint** `GET <site>/supertext/ping?challenge=…` (from `wagtail_supertext.urls`, see [Configure it](#2-configure-it)) needs no login and answers only `{"ok":true,"plugin":"Wagtail-Supertext-Translation","challenge":"…"}`, so Supertext can see that the site is reachable from the internet. It returns nothing else: no versions, no settings, no data, and it is never cached. Without the URL pattern, reports are sent without a ping address and *Settings → Supertext* says how to add it.

The plugin keeps its state (what was reported last, the latest key check, never the key) in the table `wagtail_supertext_supertextstate`, created by `migrate`.

## Who can translate

wagtail-localize's own permissions apply: editors need **Can submit translation** (*Settings → Groups → Wagtail Localize*) and edit permission on the pages. Anyone who can edit a translation can use **Translate with Supertext** in it. *Settings → Supertext* is for superusers only.

*Settings → Translation files* (*Export for translation*, *Import translation*) is open to every admin user, but lists and imports only translations of pages and snippets the user may edit (wagtail-localize's own check, the same as for *Translate with Supertext*). It needs no API key and has nothing to configure. Uploads are limited to 10 MB (your web server's request size limit must allow that much for the full limit) and are read in memory, never stored.

## Interface languages

*Settings → Supertext* and the plugin's messages are available in English, German, French and Italian. They follow each user's Wagtail admin language (*Account → Preferences → Preferred language*; `WAGTAILADMIN_PERMITTED_LANGUAGES` limits the choice). Other languages fall back to English. The `manage.py supertext_check` output is English only.

## All settings (`OPTIONS`)

| Option | Default | Purpose |
| --- | --- | --- |
| `API_KEY` | – | Supertext API key, with or without the `Supertext-Auth-Key` prefix. The `SUPERTEXT_API_KEY` environment variable wins. |
| `ENVIRONMENT` | `"live"` | `"live"`, `"staging"` or `"testing"` Supertext API |
| `ENDPOINT` | – | Custom API base URL (overrides `ENVIRONMENT`). The `SUPERTEXT_API_ENDPOINT` environment variable wins. |
| `REVIEW_URL` | Supertext's review service | Address of the review service for locales with *Translation review* on (see [Translation review](#translation-review)). `""` turns review off for every locale. The `SUPERTEXT_REVIEW_URL` environment variable wins when it is set and not empty. |
| `LANGUAGES` | `{}` | Optional override of *Settings → Supertext → Languages*, per Wagtail language code: `code`, the Supertext language (e.g. `de-CH`, `es`, `zh-Hans`), `politeness` (`more`/`less`, only for languages that offer it) and `review` (`True`/`False`, *Translation review*). Each entry wins over the choice on the settings page; see [Locales](#4-locales). |
| `TIMEOUT` | `180` | Seconds to wait for one translation |
| `POLL_INTERVAL` | `2` | Seconds between status checks |
| `VERSION_REPORTS` | `True` | *Send version information to Supertext* (see [Version information](#version-information-and-the-api-key-check)). The `SUPERTEXT_VERSION_REPORTS` environment variable (`0`/`1`) wins. |

## Updating

```bash
pip install -U "wagtail-supertext-translation @ git+https://github.com/Supertext/Wagtail-Supertext-Translation@main"
```

Then run `python manage.py migrate` (version 0.2 adds the plugin's state table, the next version the table for the languages chosen on *Settings → Supertext* and its *Translation review* column; existing locales start with review off). After an update, the next admin request that runs the plugin reports the new version to Supertext (if version information is on).

## Uninstalling

Run `python manage.py migrate wagtail_supertext zero` (drops the plugin's tables), remove `WAGTAILLOCALIZE_MACHINE_TRANSLATOR` (or point it at another translator), the `wagtail_supertext.urls` line and `"wagtail_supertext"` from `INSTALLED_APPS`, then `pip uninstall wagtail-supertext-translation`. Existing translations stay.

## Troubleshooting

| Message / symptom | Cause / fix |
| --- | --- |
| No **Translate with Supertext** button in the translation editor | `WAGTAILLOCALIZE_MACHINE_TRANSLATOR` isn't set to `wagtail_supertext.SupertextTranslator`. *Settings → Supertext* shows a warning then. The button is also missing when source and target end up as the same Supertext language. |
| *No Supertext API key is configured* | Set `SUPERTEXT_API_KEY` (or `API_KEY`) and restart. No key yet? Generate one at [supertext.com → Integrations → API](https://www.supertext.com/en/integrations/api) (Admin role). |
| *Authentication failed* | The key is wrong or revoked. Check it with **Test connection**, or generate a new one at [supertext.com → Integrations → API](https://www.supertext.com/en/integrations/api). |
| *Too many requests to Supertext* | Supertext's per-second limit was still exceeded after 4 automatic retries. Wait a moment and try again. |
| *Your Supertext translation limit is exceeded* | Your Supertext plan's volume is used up. |
| *The language … has no Supertext language yet* / **— choose —** on *Settings → Supertext* | The locale has neither a chosen Supertext language nor an obvious default (e.g. `en-ie`, `de-li`, `gsw`): choose one under *Settings → Supertext → Languages* and **Save languages** (see [Locales](#4-locales)). Nothing was sent to Supertext. |
| *Supertext does not support this language pair* | A code Supertext doesn't offer (e.g. `es-ES` instead of `es` in `LANGUAGES`, or the same language as the source). Check *Settings → Supertext → Languages* and the `LANGUAGES` → `code` entry. The message shows the codes that were sent. |
| *Supertext offers no formal or informal tone for this language* | `politeness` is set in `LANGUAGES` for a language without it (English, Swiss German, Romansh, Albanian, Serbian); remove it for that locale. |
| *Supertext rejected the API key* (API key check) | The key is wrong or revoked. Check that the whole key was copied into `SUPERTEXT_API_KEY` (or `API_KEY`), or generate a new one at [supertext.com → Integrations → API](https://www.supertext.com/en/integrations/api) (Admin role). No Supertext account yet? [Create one at supertext.com](https://www.supertext.com/person/en/account/signin). Restart, then **Test connection**. |
| *The API key couldn't be checked because Supertext can't be reached* | The server can't reach `api.supertext.com` (firewall, proxy, DNS). Translations need the same connection. |
| *Settings → Supertext* says the ping endpoint isn't installed | Add `path("supertext/", include("wagtail_supertext.urls"))` to `urls.py` before `wagtail_urls`. |
| *Timed out waiting* | Very long pages: raise `TIMEOUT`, and your web server's request timeout (e.g. gunicorn `--timeout`). |
| *Authentication failed* only for locales with *Translation review* | The review service only works with keys of the live Supertext API: with `ENVIRONMENT` `"staging"`/`"testing"` (or an `ENDPOINT`) leave review off. |
| Reviewers' corrections don't show up in Wagtail | They arrive with the next translation of each text: clear the text's translation in the translation editor and click **Translate with Supertext** (texts that already have a translation are never overwritten). Check that the locale's *Translation review* is ticked on *Settings → Supertext*. |
| *Could not reach Supertext* | The server can't make outbound HTTPS calls; check firewall or proxy (`HTTPS_PROXY`). |

Errors are shown to the editor in the translation editor and logged by Django.

## Security notes

- Prefer the `SUPERTEXT_API_KEY` environment variable: the key then never sits in your repository.
- Documents are deleted from Supertext right after download (and expire after 24 hours anyway).
