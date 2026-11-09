"""Version reports to the Supertext Commando Center, the automatic API key check and the ping endpoint.

Protocol: Commando-Center-Supertext-Translation → docs/DEVELOPER.md → *Report protocol*.
Supertext Plugin Health (``_vendor/plugin_health``) implements it (``InstallationReporter``,
``ping_response()``, ``reports_enabled()``) with the SDK's ``check_api_key()`` as the key check;
this module only gives them what Wagtail knows: the site URL, the
Wagtail and plugin versions, the ping URL, and a place for the reporter's state (the
``SupertextState`` row ``reporter``).

The settings live in Django settings, so there is no "settings saved" moment: the key check
runs when the key changed (fingerprint), on *Test connection* and after a 401/403 from
Supertext. Nothing here ever raises into a request or a translation.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import replace
from urllib.parse import urljoin

import requests
import wagtail
from django.conf import settings
from django.db import close_old_connections
from django.urls import NoReverseMatch, reverse
from django.utils.dateparse import parse_datetime
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext as _

from . import __version__
from ._vendor.plugin_health import (
    CallableKeyChecker,
    HttpRequest,
    HttpResponse,
    InstallationReporter,
    KeyCheck,
    NameVersion,
    ReporterResult,
    ReporterState,
    ping_response,
    reporter_state,
)
from ._vendor.supertext import SDK_VERSION
from ._vendor.supertext import v1 as sdk
from .client import API_KEY_URL, PLUGIN_NAME, SIGNUP_URL, RequestsTransport, user_agent

logger = logging.getLogger(__name__)

STATE_KEY = "reporter"

#: Runs reporter actions after the request in a thread; tests set it to False (same thread).
BACKGROUND = True
_lock = threading.Lock()


def transport():
    """How reports and the key check reach the network (``requests``: proxies, certificates). Tests replace it."""
    return RequestsTransport(requests.Session())


# --------------------------------------------------------------------------------- site


def site_url(request=None) -> str | None:
    """The site's base URL: WAGTAILADMIN_BASE_URL, else the request's, else the default site's root URL."""
    configured = str(getattr(settings, "WAGTAILADMIN_BASE_URL", "") or "").strip()
    if configured.lower().startswith(("http://", "https://")):
        return configured.rstrip("/")
    if request is not None:
        try:
            return request.build_absolute_uri("/").rstrip("/")
        except Exception:  # DisallowedHost and friends
            pass
    try:
        from wagtail.models import Site

        site = Site.objects.filter(is_default_site=True).first()
        if site and site.root_url.lower().startswith(("http://", "https://")):
            return site.root_url.rstrip("/")
    except Exception:
        pass
    return None


def callback_url(base: str) -> str | None:
    """Absolute URL of the ping endpoint, or None when the site's urls.py doesn't include wagtail_supertext.urls."""
    try:
        return urljoin(base + "/", reverse("wagtail_supertext_ping"))
    except NoReverseMatch:
        return None


# --------------------------------------------------------------------------------- state


def load_state() -> ReporterState:
    from .models import SupertextState

    row = SupertextState.objects.filter(key=STATE_KEY).first()
    return reporter_state(row.value if row else None)


def save_state(state: ReporterState) -> None:
    from .models import SupertextState

    SupertextState.objects.update_or_create(key=STATE_KEY, defaults={"value": state.to_json()})


def last_check() -> KeyCheck | None:
    """The latest stored key check (for *Settings → Supertext*), or None."""
    try:
        return load_state().key_check
    except Exception as error:
        logger.debug("Supertext: could not read the reporter state: %s", error)
        return None


# ------------------------------------------------------------------------------ reporter


def key_client(options, api_key: str, http) -> sdk.SupertextClient:
    """The SDK client for the key check (the configured API URL, the plugin's user agent and transport)."""
    return sdk.SupertextClient(
        api_key, base_url=options.base_url, user_agent=user_agent(), transport=http
    )


def reporter(options, request=None) -> InstallationReporter | None:
    url = site_url(request)
    if not url:
        return None
    http = transport()

    def send(report: HttpRequest) -> HttpResponse:
        answer = http(sdk.HttpRequest(report.method, report.url, dict(report.headers), report.body, report.timeout))
        return HttpResponse(answer.status, answer.body)

    return InstallationReporter(
        enabled=options.reports_enabled,
        site_url=url,
        plugin=NameVersion(PLUGIN_NAME, __version__),
        system=NameVersion("Wagtail", wagtail.__version__),
        sdk_version=SDK_VERSION,
        callback_url=callback_url(url),
        transport=send,
        key_checker=CallableKeyChecker(
            check=lambda api_key: key_client(options, api_key, http).check_api_key(),
            fingerprint=sdk.api_key_fingerprint,
        ),
    )


def run(action: str, options, request=None, http_status: int = 401) -> ReporterResult | None:
    """Runs one reporter action with the stored state and stores the new state. Never raises.

    ``action``: ``on_request``, ``settings_saved``, ``test_connection``, ``authentication_failed`` or ``report_check``.
    """
    with _lock:
        try:
            current = reporter(options, request)
            state = load_state()
            if current is None:
                logger.debug("Supertext: no site URL, version report skipped")
                if action != "test_connection":
                    return None
                # The key check doesn't need a site URL: run it alone and keep the result.
                check = KeyCheck.from_value(key_client(options, options.api_key, transport()).check_api_key())
                state = replace(state, key_check=check)
                save_state(state)
                return ReporterResult(state, state.key_check, None, False)
            if action == "test_connection":
                result = current.test_connection(state, options.api_key)
            elif action == "settings_saved":
                result = current.after_settings_saved(state, options.api_key)
            elif action == "authentication_failed":
                result = current.authentication_failed(state, options.api_key, http_status)
            elif action == "report_check":
                result = current.report_check(state)
            else:
                result = current.on_request(state, options.api_key)
            save_state(result.state)
            if result.event:
                logger.debug("Supertext: version report %s %s", result.event, "sent" if result.sent else "not sent")
            return result
        except Exception as error:  # reporting is optional: never break the caller
            logger.debug("Supertext: version report skipped: %s", error)
            return None


def in_background(action: str, options, request=None, http_status: int = 401) -> None:
    """For back-end requests: runs the action in a thread, so the request never waits for it."""
    if not BACKGROUND:
        run(action, options, request, http_status)
        return

    # The request object is only read for its host before the thread starts.
    url = site_url(request)

    def work():
        try:
            run(action, options, _FixedSite(url) if url else None, http_status)
        finally:
            close_old_connections()

    try:
        threading.Thread(target=work, name="supertext-report", daemon=True).start()
    except Exception as error:
        logger.debug("Supertext: could not start the version report: %s", error)


class _FixedSite:
    """Stands in for the request in the background thread (only its base URL is used)."""

    def __init__(self, url: str):
        self.url = url

    def build_absolute_uri(self, location="/"):
        return self.url + "/"


def ping(challenge) -> tuple[int, dict[str, str], str]:
    """The ping endpoint's answer (status, headers, body), exactly as Plugin Health builds it."""
    from . import conf

    try:
        enabled = conf.load().reports_enabled
    except Exception:
        enabled = False
    response = ping_response(challenge if isinstance(challenge, str) else None, PLUGIN_NAME, enabled)
    return response.status, dict(response.headers), response.body


# ------------------------------------------------------------------------------ messages


def check_message(check: KeyCheck | None) -> str:
    """The key check as a message in the admin language (account and key links for invalid/missing)."""
    if check is None:
        return ""
    links = {
        "signup_link": format_html('<a href="{}" target="_blank" rel="noopener">supertext.com</a>', SIGNUP_URL),
        "api_key_link": format_html('<a href="{}" target="_blank" rel="noopener">supertext.com → Integrations → API</a>', API_KEY_URL),
    }
    if check.status == "ok":
        return _("API key checked: the connection to Supertext works.")
    if check.status == "invalid":
        text = _(
            "Supertext rejected the API key. Check that you copied the whole key, or generate a new one at "
            "%(api_key_link)s (requires the Admin role). No Supertext account yet? Create one at %(signup_link)s."
        )
        return mark_safe(text % links)
    if check.status == "missing":
        text = _(
            "No API key is set yet. No Supertext account yet? Create one at %(signup_link)s. "
            "Generate your API key at %(api_key_link)s (requires the Admin role)."
        )
        return mark_safe(text % links)
    if check.status == "unreachable":
        return _("The API key couldn't be checked because Supertext can't be reached. The settings are saved; check again later.")
    status = check.http_status or check.error_code or "?"
    return _(
        "The API key couldn't be checked: Supertext answered with an error (%(status)s). The settings are saved; check again later."
    ) % {"status": status}


def checked_at(check: KeyCheck | None):
    """When the check ran, as an aware datetime (for the template's date filter)."""
    return parse_datetime(check.checked_at) if check and check.checked_at else None
