"""Plugin options: the OPTIONS of WAGTAILLOCALIZE_MACHINE_TRANSLATOR, plus environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ._vendor import plugin_health
from ._vendor.supertext import v1 as sdk
from ._vendor.supertext.v1 import Languages
from .client import REVIEW_URL, SupertextClient, SupertextError, base_url_for, normalize_key

TRANSLATOR_CLASS = "wagtail_supertext.SupertextTranslator"


@dataclass
class Options:
    api_key: str = ""
    api_key_source: str = ""  # "environment", "settings" or ""
    environment: str = "live"
    endpoint: str = ""
    review_url: str = REVIEW_URL  # the REVIEW_URL option / SUPERTEXT_REVIEW_URL ("" = review off everywhere)
    languages: dict[str, dict[str, str]] = field(default_factory=dict)  # the LANGUAGES option (wins)
    saved: dict[str, dict[str, str]] = field(default_factory=dict)  # Settings → Supertext → Languages
    timeout: float = 180
    poll_interval: float = 2
    version_reports: bool | None = None  # the VERSION_REPORTS option (None: not set, default on)

    @property
    def base_url(self) -> str:
        return base_url_for(self.environment, self.endpoint)

    @property
    def mapping(self) -> dict[str, str]:
        """Wagtail language code → Supertext code: the codes chosen under *Settings → Supertext*, with the
        ``code`` entries of the LANGUAGES option winning over them."""
        result: dict[str, str] = {}
        for source in (self.saved, self.languages):
            for key, value in source.items():
                code = str((value or {}).get("code") or "").strip()
                if code:
                    result = {k: v for k, v in result.items() if _key(k) != _key(key)}
                    result[key] = code
        return result

    def override_code(self, language_code: str) -> str:
        """The LANGUAGES option's ``code`` for a locale ("" if none): it wins over the settings page."""
        return str(_lookup(self.languages, language_code).get("code") or "").strip()

    def override_politeness(self, language_code: str) -> str:
        """The LANGUAGES option's ``politeness`` for a locale ("" if none): it wins over the settings page."""
        value = str(_lookup(self.languages, language_code).get("politeness") or "").strip()
        return value if value in ("default", "more", "less") else ""

    @property
    def reports_enabled(self) -> bool:
        """*Send version information to Supertext*: SUPERTEXT_VERSION_REPORTS wins over VERSION_REPORTS (default on)."""
        return plugin_health.reports_enabled(self.version_reports)

    def target_code(self, language_code: str) -> str:
        """Supertext target for a Wagtail language, without guessing.

        The mapping (LANGUAGES → ``code``, else the code chosen under *Settings → Supertext*) wins
        (sent as it is); else a Wagtail code that is a Supertext code apart from case and ``_``/``-``
        (``de-ch`` → ``de-CH``, ``es``); else the SDK's default (``de`` → ``de-DE``, ``es-es`` → ``es``);
        else :class:`SupertextError` ``unsupported_language`` (nothing is sent).
        """
        try:
            return Languages.target(language_code, self.mapping)
        except sdk.SupertextError as error:
            raise SupertextError.from_sdk(error) from error

    def target_code_or_none(self, language_code: str) -> str | None:
        """The target code, or ``None`` for a language without mapping and without default."""
        try:
            return self.target_code(language_code)
        except SupertextError:
            return None

    def source_code(self, language_code: str) -> str:
        """Supertext source for a Wagtail language: the LANGUAGES mapping in its source form (one mapping for
        both directions: ``de-CH`` → ``de``), else the code's language (``en-us`` → ``en``), else
        ``unsupported_language``."""
        try:
            return Languages.source(language_code, self.mapping)
        except sdk.SupertextError as error:
            raise SupertextError.from_sdk(error) from error

    def politeness(self, language_code: str) -> str:
        """Formal/informal: the LANGUAGES option wins, else the tone chosen under *Settings → Supertext*."""
        value = self.override_politeness(language_code) or str(_lookup(self.saved, language_code).get("politeness") or "")
        return value if value in ("more", "less") else "default"

    def override_review(self, language_code: str) -> bool | None:
        """The LANGUAGES option's ``review`` for a locale (``None`` if not set): it wins over the settings page."""
        return _flag(_lookup(self.languages, language_code).get("review"))

    def review(self, language_code: str) -> bool:
        """*Translation review* for translations into this locale: the LANGUAGES option wins, else the
        checkbox under *Settings → Supertext*. Off for every locale when ``review_url`` is empty."""
        if not self.review_available:
            return False
        override = self.override_review(language_code)
        if override is not None:
            return override
        return bool(_flag(_lookup(self.saved, language_code).get("review")))

    @property
    def review_available(self) -> bool:
        """A review service is configured and is not the API itself (then there is no second client)."""
        url = self.review_url.strip()
        return bool(url) and url.rstrip("/") != self.base_url.rstrip("/")

    def politeness_supported(self, language_code: str) -> bool | None:
        """Whether Supertext offers formal/informal for the locale's target (``None``: unknown or unmapped)."""
        target = self.target_code_or_none(language_code)
        return Languages.supports_politeness(target) if target else None

    def language(self, language_code: str) -> dict[str, str]:
        return _lookup(self.languages, language_code)

    def client(self, review: bool = False, **kwargs) -> SupertextClient:
        """The Supertext client; with ``review=True`` (a locale with *Translation review* on) a client with the
        same key and settings whose base URL is the review service. Without a review service, the direct one."""
        base_url = self.review_url.strip() if review and self.review_available else self.base_url
        return SupertextClient(
            self.api_key, base_url, poll_timeout=self.timeout, poll_interval=self.poll_interval, **kwargs
        )


def _key(code: str) -> str:
    return code.strip().replace("_", "-").lower()


def _lookup(table: dict[str, dict[str, str]], language_code: str) -> dict[str, str]:
    wanted = _key(language_code)
    for key, value in table.items():
        if _key(key) == wanted:
            return value or {}
    return {}


def saved_languages() -> dict[str, dict[str, str]]:
    """The languages chosen under *Settings → Supertext*: Wagtail language code → ``code``/``politeness``/``review``.

    Empty when the database can't be read (not migrated yet, no database in a unit test): translating
    then works with the LANGUAGES option and the SDK's defaults alone.
    """
    try:
        from django.db import transaction

        from .models import SupertextLanguage

        with transaction.atomic():  # a savepoint: a missing table doesn't break the surrounding transaction
            return {
                row.locale.language_code: {"code": row.code, "politeness": row.politeness, "review": row.review}
                for row in SupertextLanguage.objects.select_related("locale")
            }
    except Exception:  # noqa: BLE001 - a missing table or blocked database must never break a translation
        return {}


def bcp47(language_code: str) -> str:
    """de-ch -> de-CH, zh-hans -> zh-Hans, fr -> fr (for display)."""
    parts = language_code.replace("_", "-").split("-")
    out = [parts[0].lower()]
    for part in parts[1:]:
        out.append(part.upper() if len(part) == 2 else part.capitalize())
    return "-".join(out)


def _flag(value) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "off", "no")
    return bool(value)


def load(options: dict | None = None, saved: dict[str, dict[str, str]] | None = None) -> Options:
    """Options from the given dict (or Django settings), with SUPERTEXT_API_KEY / SUPERTEXT_API_ENDPOINT /
    SUPERTEXT_REVIEW_URL winning (when set and not empty),
    and the languages chosen under *Settings → Supertext* (read from the database unless ``saved`` is given)."""
    if options is None:
        from django.conf import settings

        config = getattr(settings, "WAGTAILLOCALIZE_MACHINE_TRANSLATOR", None) or {}
        options = config.get("OPTIONS", {}) if config.get("CLASS") == TRANSLATOR_CLASS else {}

    env_key = os.environ.get("SUPERTEXT_API_KEY", "")
    key = env_key or str(options.get("API_KEY") or "")
    return Options(
        api_key=normalize_key(key),
        api_key_source="environment" if env_key else ("settings" if key else ""),
        environment=str(options.get("ENVIRONMENT") or "live"),
        endpoint=os.environ.get("SUPERTEXT_API_ENDPOINT", "") or str(options.get("ENDPOINT") or ""),
        review_url=os.environ.get("SUPERTEXT_REVIEW_URL", "").strip()
        or (str(options.get("REVIEW_URL") or "").strip() if "REVIEW_URL" in options else REVIEW_URL),
        languages={str(k): dict(v or {}) for k, v in (options.get("LANGUAGES") or {}).items()},
        saved=saved_languages() if saved is None else saved,
        timeout=float(options.get("TIMEOUT") or 180),
        poll_interval=float(options.get("POLL_INTERVAL") or 2),
        version_reports=_flag(options.get("VERSION_REPORTS")),
    )
