"""
The plugin's access to the Supertext AI file translation API (v1), built on the shared
Supertext Python SDK (``wagtail_supertext/_vendor/supertext``, copied by
``scripts/vendor-sdk.sh``; sites install nothing else). The SDK does the protocol:

1. ``POST translate/ai/file``                 multipart HTML upload -> ``{"file_id": …}``
2. ``GET translate/ai/file/{id}/status``      poll until ``done``
3. ``GET translate/ai/file/{id}/translation`` the translated HTML
4. ``DELETE translate/ai/file/{id}``          cleanup, also after errors

plus the ``Authorization: Supertext-Auth-Key <key>`` header (a pasted prefix is stripped),
HTTP 429 retries, language codes and packing many strings into one document. Requests go
through ``requests`` (the plugin's HTTP library: proxies, certificates). SDK errors come out
as this module's :class:`SupertextError`, whose message template is translated in the admin.

No Django imports here: tested on its own.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

import requests

from ._vendor.supertext import SDK_VERSION
from ._vendor.supertext import v1 as sdk
from ._vendor.supertext.v1 import (
    ErrorCode,
    HttpRequest,
    HttpResponse,
    Languages,
    Segment,
    html_document,
    plan_batches,
)

LIVE = sdk.LIVE_BASE_URL
ENVIRONMENTS = {
    "live": LIVE,
    "staging": "https://api.staging.supertext.com/v1/",
    "testing": "https://api.testing.supertext.com/v1/",
}
#: Supertext's review service (the translation proxy of Supertext Localization Review). It speaks the
#: same API v1 and forwards to the live Supertext API with the plugin's key, keeping every translation
#: so reviewers can correct them on the website. Used for locales with *Translation review* on.
REVIEW_URL = "https://localizationreview-production.up.railway.app/v1/"
#: The GitHub repository name: the User-Agent (with the version) and the version reports name the plugin by it.
PLUGIN_NAME = "Wagtail-Supertext-Translation"
SIGNUP_URL = "https://www.supertext.com/person/en/account/signin"
#: supertext.com → Integrations → API (requires the Admin role).
API_KEY_URL = "https://www.supertext.com/en/integrations/api"

#: Stay well below the API's 1,000,000 character limit per document.
MAX_DOCUMENT_CHARACTERS = sdk.DEFAULT_MAX_CHARACTERS

__all__ = ["REVIEW_URL", "SDK_VERSION", "Languages", "SupertextClient", "SupertextError", "user_agent"]


def gettext_noop(message: str) -> str:
    """Marks a message for translation (makemessages) without importing Django."""
    return message


_KEY_LINKS = {"signup_url": SIGNUP_URL, "api_key_url": API_KEY_URL}

#: Message (translated in the admin) per SDK error code.
MESSAGES: dict[str, str] = {
    ErrorCode.MISSING_API_KEY: gettext_noop(
        "No Supertext API key is configured. No Supertext account yet? Create one at %(signup_url)s. "
        "Generate your API key at %(api_key_url)s (supertext.com → Integrations → API, requires the Admin role)."
    ),
    ErrorCode.AUTHENTICATION_FAILURE: gettext_noop(
        "Authentication failed. Please check the Supertext API key. No Supertext account yet? Create one at %(signup_url)s. "
        "Generate a new API key at %(api_key_url)s (requires the Admin role)."
    ),
    ErrorCode.INVALID_LANGUAGE_PAIR: gettext_noop(
        "Supertext does not support this language pair. Check the languages under Settings → Supertext → Languages."
    ),
    ErrorCode.UNSUPPORTED_LANGUAGE: gettext_noop(
        "The language %(language)s has no Supertext language yet. Ask your administrator to choose one "
        "under Settings → Supertext → Languages."
    ),
    ErrorCode.UNSUPPORTED_FEATURE: gettext_noop(
        "Supertext offers no formal or informal tone for this language. Set it to the default tone "
        "under Settings → Supertext → Languages (or remove politeness from the LANGUAGES option)."
    ),
    ErrorCode.NOT_FOUND: gettext_noop("The requested Supertext resource was not found."),
    ErrorCode.PAYLOAD_TOO_LARGE: gettext_noop("The content is too large for Supertext to translate in one go."),
    ErrorCode.TOO_MANY_REQUESTS: gettext_noop("Too many requests to Supertext. Please try again shortly."),
    ErrorCode.SERVICE_UNAVAILABLE: gettext_noop("The Supertext service is currently unavailable."),
    ErrorCode.UNEXPECTED_STATUS: gettext_noop("Supertext answered with HTTP %(code)s."),
    ErrorCode.TRANSPORT_ERROR: gettext_noop("Could not reach Supertext: %(error)s"),
    ErrorCode.NO_FILE_ID: gettext_noop("Supertext did not return a file id."),
    ErrorCode.TRANSLATION_ERROR: gettext_noop("Supertext could not translate the document."),
    ErrorCode.QUOTA_EXCEEDED: gettext_noop("Your Supertext translation limit is exceeded. Please upgrade your subscription."),
    ErrorCode.FILE_DELETED: gettext_noop("The document was deleted at Supertext before it could be downloaded."),
    ErrorCode.TIMEOUT: gettext_noop("Timed out waiting for the Supertext translation."),
    ErrorCode.INCOMPLETE_RESPONSE: gettext_noop("The translated document was empty."),
}


class SupertextError(Exception):
    """A Supertext error. ``code`` is the SDK's error code.

    ``str(error)`` is the English message. ``template`` and ``params`` let the Django layer
    show it in the admin language; ``detail`` is the API's own text (never translated).
    """

    def __init__(self, message: str, status: int | None = None, *, params: dict | None = None, detail: str = "", code: str = ""):
        self.template = message
        self.params = params or {}
        self.status = status
        self.detail = detail
        self.code = code
        text = message % self.params if self.params else message
        super().__init__(f"{text} ({detail})" if detail else text)

    @classmethod
    def from_sdk(cls, error: sdk.SupertextError) -> SupertextError:
        params: dict = {}
        detail = error.detail or ""
        if error.code in (ErrorCode.MISSING_API_KEY, ErrorCode.AUTHENTICATION_FAILURE):
            params = dict(_KEY_LINKS)
        elif error.code == ErrorCode.UNEXPECTED_STATUS:
            params = {"code": error.status}
        elif error.code == ErrorCode.TRANSPORT_ERROR:
            params, detail = {"error": detail or "?"}, ""
        elif error.code == ErrorCode.UNSUPPORTED_LANGUAGE:
            params, detail = {"language": error.target_lang or error.source_lang or "?"}, ""
        if error.code in (ErrorCode.INVALID_LANGUAGE_PAIR, ErrorCode.UNSUPPORTED_FEATURE) and (error.source_lang or error.target_lang):
            detail = f"{error.source_lang or '?'} → {error.target_lang or '?'}"
        message = MESSAGES.get(error.code, error.message)
        return cls(message, error.status, params=params, detail=detail, code=error.code)


def normalize_key(key: str | None) -> str:
    """Strips whitespace and a pasted ``Supertext-Auth-Key `` prefix."""
    key = (key or "").strip()
    return key[len("Supertext-Auth-Key ") :].strip() if key.lower().startswith("supertext-auth-key ") else key


def base_url_for(environment: str = "live", endpoint: str = "") -> str:
    url = (endpoint or "").strip() or ENVIRONMENTS.get((environment or "live").lower(), LIVE)
    return url.rstrip("/") + "/"


def user_agent() -> str:
    """``Wagtail-Supertext-Translation/<version>``: Supertext and the review service see which release calls."""
    from . import __version__

    return f"{PLUGIN_NAME}/{__version__}"


class RequestsTransport:
    """Sends the SDK's requests with ``requests`` (honours proxies and certificate settings)."""

    def __init__(self, session: requests.Session):
        self.session = session

    def __call__(self, request: HttpRequest) -> HttpResponse:
        try:
            response = self.session.request(
                request.method, request.url, headers=dict(request.headers), data=request.body, timeout=request.timeout
            )
        except requests.RequestException as error:
            raise OSError(str(error)) from error
        headers = {name.lower(): value for name, value in response.headers.items()}
        return HttpResponse(response.status_code, response.content or b"", headers)


class SupertextClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = LIVE,
        *,
        poll_timeout: float = 180,
        poll_interval: float = 2,
        request_timeout: float = 30,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = normalize_key(api_key)
        self.base_url = (base_url or LIVE).rstrip("/") + "/"
        self.transport = RequestsTransport(session or requests.Session())
        self.sdk = sdk.SupertextClient(
            self.api_key,
            base_url=self.base_url,
            poll_interval=poll_interval,
            timeout=poll_timeout,
            request_timeout=request_timeout,
            user_agent=user_agent(),
            transport=self.transport,
            sleep=sleep,
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def translate_document(self, html: str, target_language: str, source_language: str = "", politeness: str = "default") -> str:
        """Translates a complete HTML document and returns the translated HTML.

        The codes are sent as given: Supertext codes, resolved by ``conf.Options`` (``de-CH``,
        ``es``; source ``en``, empty: auto-detect); ``politeness`` is default, more or less.
        """
        try:
            return self.sdk.translate_html(html, target_language, source_language or None, politeness)  # type: ignore[arg-type]
        except sdk.SupertextError as error:
            raise SupertextError.from_sdk(error) from error

    def translate_strings(
        self, strings: Sequence[str], target_language: str, source_language: str = "", politeness: str = "default"
    ) -> list[str | None]:
        """Translates HTML fragments (one ``data-st-id`` element each) in as few documents as possible.

        Returns the translations in input order; ``None`` where Supertext returned nothing for a string.
        """
        segments = {str(i): Segment(text, html=True) for i, text in enumerate(strings)}
        out: list[str | None] = [None] * len(strings)
        for batch in plan_batches(segments, MAX_DOCUMENT_CHARACTERS):
            batch_segments = [segments[key] for key in batch]
            translated = self.translate_document(html_document.build(batch_segments), target_language, source_language, politeness)
            for key, text in zip(batch, html_document.parse(translated, batch_segments), strict=True):
                out[int(key)] = text
        return out

    def validate_api_key(self) -> None:
        """Cost-free check that the key is accepted."""
        try:
            self.sdk.validate_api_key()
        except sdk.SupertextError as error:
            raise SupertextError.from_sdk(error) from error
