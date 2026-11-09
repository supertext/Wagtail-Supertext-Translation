"""The wagtail-localize machine translator."""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.utils.text import slugify
from wagtail_localize.machine_translators.base import BaseMachineTranslator
from wagtail_localize.strings import StringValue

from . import conf, reporting
from ._vendor.supertext import v1 as sdk
from .client import Languages, SupertextError

logger = logging.getLogger(__name__)

#: Page slugs reach the translator as plain strings like "swiss-chocolate-shipped-worldwide".
#: Lower-case words joined by at least two hyphens are treated as slugs: translated as words,
#: then turned back into a slug, so translated pages get translated URLs.
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")


def outgoing(data: str) -> tuple[str, bool]:
    """The text to translate for a wagtail-localize string and whether it is a slug (sent as words)."""
    is_slug = bool(SLUG.match(data))
    return (data.replace("-", " ") if is_slug else data), is_slug


def incoming(value: str, is_slug: bool) -> StringValue | None:
    """The translated text as a wagtail-localize value (a slug again for slugs), or None when there
    is nothing usable (empty, or HTML wagtail-localize doesn't allow)."""
    if not value:
        return None
    if is_slug:
        slug = slugify(StringValue(value).render_text(), allow_unicode=getattr(settings, "WAGTAIL_ALLOW_UNICODE_SLUGS", True))
        return StringValue.from_plaintext(slug) if slug else None
    try:
        return StringValue.from_translated_html(value)
    except Exception:  # wagtail-localize rejects tags it doesn't allow
        logger.warning("Supertext returned HTML wagtail-localize can't use for %r; left untranslated", value[:80])
        return None


class SupertextTranslator(BaseMachineTranslator):
    """
    WAGTAILLOCALIZE_MACHINE_TRANSLATOR = {
        "CLASS": "wagtail_supertext.SupertextTranslator",
        "OPTIONS": {"API_KEY": os.environ.get("SUPERTEXT_API_KEY")},
    }

    wagtail-localize calls ``translate()`` with every string of a page that has no
    translation yet (each a paragraph or field value as inline HTML). They go to Supertext
    as one document, one ``<div data-st-id>`` per string (packed by the Supertext SDK).
    """

    display_name = "Supertext"

    def __init__(self, options):
        super().__init__(options)
        self.config = conf.load(options or {})

    def can_translate(self, source_locale, target_locale):
        """False only when both resolve to the same language. An unmapped language keeps the button,
        so the editor gets the message that it has no Supertext language yet."""
        try:
            source = self.config.source_code(source_locale.language_code)
            target = self.config.target_code(target_locale.language_code)
        except SupertextError:
            return True
        return source.lower() != target.split("-")[0].lower()

    def translate(self, source_locale, target_locale, strings):
        strings = list(strings)
        if not strings:
            return {}

        source = self.config.source_code(source_locale.language_code)
        target = self.config.target_code(target_locale.language_code)
        politeness = self.config.politeness(target_locale.language_code)
        if politeness != "default" and Languages.supports_politeness(target) is False:
            # Supertext would reject it (FEATURE_ERROR); say so instead of silently dropping the setting.
            raise SupertextError.from_sdk(
                sdk.SupertextError(sdk.ErrorCode.UNSUPPORTED_FEATURE, feature="politeness", source_lang=source, target_lang=target)
            )
        # A locale with *Translation review* on goes through Supertext's review service; the SDK's round trip
        # (upload, status, download, delete) runs on that one client, so the job is collected where it was sent.
        client = self.client(review=self.config.review(target_locale.language_code))
        prepared = [outgoing(string.data) for string in strings]
        result = {}

        try:
            translations = client.translate_strings([text for text, _slug in prepared], target, source, politeness)
        except SupertextError as error:
            if error.code == sdk.ErrorCode.AUTHENTICATION_FAILURE:
                reporting.in_background("authentication_failed", self.config, http_status=error.status or 401)
            raise
        reporting.in_background("on_request", self.config)
        for index, value in enumerate(translations):
            translated = incoming(value or "", prepared[index][1])
            if translated is not None:
                result[strings[index]] = translated

        return result

    def client(self, review=False):
        return self.config.client(review=review)
