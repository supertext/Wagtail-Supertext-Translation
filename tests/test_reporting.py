"""Commando Center glue: the setting, state storage, when the reporter runs, the ping endpoint."""

import json

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from wagtail.models import Locale

from wagtail_supertext import __version__, conf, reporting
from wagtail_supertext.models import SupertextState

pytestmark = pytest.mark.django_db
CHALLENGE = "abcdefghijklmnop0123"


@pytest.fixture
def admin(client):
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    client.force_login(user)
    return client


def stored():
    return json.loads(SupertextState.objects.get(key="reporter").value)


def test_first_admin_request_reports_installed(admin, center):
    admin.get("/admin/supertext/")
    (report,) = center.reports
    assert report["event"] == "installed"
    assert report["site_url"] == "http://testserver"
    assert report["plugin"] == {"name": "Wagtail-Supertext-Translation", "version": __version__}
    assert report["system"]["name"] == "Wagtail" and report["runtime"]["name"] == "Python"
    assert report["sdk_version"] == "0.5.0"
    assert report["api_key_configured"] is True and report["api_key_check"]["status"] == "ok"
    assert report["callback"] == {"url": "http://testserver/supertext/ping"}
    assert "test-key" not in json.dumps(report)
    state = stored()
    assert state["reportedKeyStatus"] == "ok" and state["keyCheck"]["status"] == "ok"
    assert "test-key" not in SupertextState.objects.get(key="reporter").value

    # Nothing changed: no new report, no new key check.
    requests_before = len(center.requests)
    admin.get("/admin/supertext/")
    assert len(center.requests) == requests_before


def test_state_stored_by_the_sdk_reporter_is_kept(admin, center):
    # Stored by the Supertext SDK 0.1-0.2 reporter (same row, same JSON): no new installed report.
    import platform

    import wagtail

    from wagtail_supertext._vendor.supertext.v1 import api_key_fingerprint

    old = {
        "reportedVersions": f"{__version__}|{wagtail.__version__}|{platform.python_version()}",
        "reportedKeyStatus": "ok",
        "lastFailureAt": None,
        "keyCheck": {"status": "ok", "checkedAt": "2026-10-01T08:00:00Z", "httpStatus": 200, "errorCode": None, "fingerprint": api_key_fingerprint("test-key")},
    }
    SupertextState.objects.create(key="reporter", value=json.dumps(old, separators=(",", ":")))
    admin.get("/admin/supertext/")
    assert center.requests == [] and stored() == old


def test_bundled_plugin_health():
    from pathlib import Path

    from wagtail_supertext._vendor import plugin_health, supertext

    vendor = Path(reporting.__file__).parent / "_vendor"
    assert (vendor / "plugin_health" / "VERSION").read_text().strip() == plugin_health.PLUGIN_HEALTH_VERSION == "0.1.0"
    assert not hasattr(supertext.v1, "InstallationReporter")


def test_upgrade_is_reported(admin, center):
    admin.get("/admin/supertext/")
    state = stored()
    state["reportedVersions"] = "0.0.1|" + state["reportedVersions"].split("|", 1)[1]
    SupertextState.objects.filter(key="reporter").update(value=json.dumps(state))
    admin.get("/admin/supertext/")
    assert [r["event"] for r in center.reports] == ["installed", "upgraded"]


def test_status_page_shows_check_and_setting(admin):
    admin.get("/admin/supertext/")
    html = admin.get("/admin/supertext/").content.decode()
    assert "API key checked: the connection to Supertext works." in html
    assert "Send version information to Supertext" in html and "<strong>On</strong>" in html
    assert "http://testserver/supertext/ping" in html


def test_test_connection_shows_rejected_key_with_links(admin, center, monkeypatch):
    monkeypatch.setenv("SUPERTEXT_API_KEY", "wrong-key")
    response = admin.post("/admin/supertext/", follow=True)
    text = " ".join(str(m) for m in response.context["messages"])
    assert "Supertext rejected the API key" in text
    assert 'href="https://www.supertext.com/en/integrations/api" target="_blank" rel="noopener"' in text
    assert 'href="https://www.supertext.com/person/en/account/signin"' in text
    assert stored()["keyCheck"]["status"] == "invalid"
    assert center.reports[-1]["api_key_check"]["status"] == "invalid"


def test_check_messages_in_the_admin_language():
    from django.utils import translation

    check = reporting.KeyCheck("error", "2026-10-13T08:00:00Z", 418, None, "x")
    with translation.override("de"):
        assert "Supertext hat mit einem Fehler geantwortet (418)" in reporting.check_message(check)
    with translation.override("it"):
        assert reporting.check_message(reporting.KeyCheck("ok", "2026-10-13T08:00:00Z", 200, None, "x")).startswith("Chiave API verificata")


def test_reports_off_sends_nothing_but_still_checks_the_key(admin, center, monkeypatch, client):
    monkeypatch.setenv("SUPERTEXT_VERSION_REPORTS", "0")
    admin.post("/admin/supertext/")
    assert center.reports == []
    assert stored()["keyCheck"]["status"] == "ok"
    html = admin.get("/admin/supertext/").content.decode()
    assert "<strong>Off</strong>" in html and "SUPERTEXT_VERSION_REPORTS environment variable" in html
    assert client.get(f"/supertext/ping?challenge={CHALLENGE}").status_code == 404


def test_version_reports_option(settings, center, admin):
    settings.WAGTAILLOCALIZE_MACHINE_TRANSLATOR = {**settings.WAGTAILLOCALIZE_MACHINE_TRANSLATOR, "OPTIONS": {"API_KEY": "test-key", "VERSION_REPORTS": False}}
    admin.get("/admin/supertext/")
    assert center.reports == []
    assert conf.load().reports_enabled is False


def test_center_failure_never_breaks_and_backs_off(admin, center):
    center.center_status = None  # unreachable
    assert admin.get("/admin/supertext/").status_code == 200
    assert stored()["lastFailureAt"]
    center.center_status = 202
    admin.get("/admin/supertext/")
    assert center.reports == [] or all(r["event"] == "installed" for r in center.reports)
    assert len([r for r in center.requests if r.url.endswith("/report")]) == 1  # no retry within the hour


def test_storage_failure_never_breaks(admin, monkeypatch):
    def broken():
        raise RuntimeError("database down")

    monkeypatch.setattr(reporting, "load_state", broken)
    assert admin.get("/admin/supertext/").status_code == 200
    assert reporting.run("on_request", conf.load()) is None


def test_ping_endpoint(client):
    response = client.get(f"/supertext/ping?challenge={CHALLENGE}")
    assert response.status_code == 200
    assert json.loads(response.content) == {"ok": True, "plugin": "Wagtail-Supertext-Translation", "challenge": CHALLENGE}
    assert response["Content-Type"] == "application/json" and response["Cache-Control"] == "no-store"
    assert client.get("/supertext/ping?challenge=<script>").status_code == 400
    assert client.get("/supertext/ping").status_code == 400
    assert client.post(f"/supertext/ping?challenge={CHALLENGE}").status_code == 405


def test_authentication_failure_in_a_translation_is_reported_once(center):
    options = conf.load()
    reporting.run("on_request", options)
    reporting.run("authentication_failed", options, http_status=403)
    reporting.run("authentication_failed", options, http_status=403)
    assert [r["event"] for r in center.reports] == ["installed", "api_key_check"]
    assert center.reports[-1]["api_key_check"] == {"status": "invalid", "checked_at": stored()["keyCheck"]["checkedAt"], "http_status": 403}


def test_check_command(center):
    call_command("supertext_check")
    assert [r["event"] for r in center.reports] == ["installed", "check"]


def test_check_command_fails_for_a_rejected_key(center, monkeypatch):
    monkeypatch.setenv("SUPERTEXT_API_KEY", "wrong-key")
    with pytest.raises(CommandError, match="invalid"):
        call_command("supertext_check")


def test_site_url_from_wagtail_settings(settings, rf):
    settings.WAGTAILADMIN_BASE_URL = "https://cms.example.ch/"
    assert reporting.site_url() == "https://cms.example.ch"
    assert reporting.callback_url("https://cms.example.ch") == "https://cms.example.ch/supertext/ping"
    settings.WAGTAILADMIN_BASE_URL = ""
    settings.ALLOWED_HOSTS = ["wagtail.example.ch"]
    assert reporting.site_url(rf.get("/", HTTP_HOST="wagtail.example.ch")) == "http://wagtail.example.ch"


def test_status_page_language_selects(admin, settings):
    es = Locale.objects.get_or_create(language_code="es-es")[0]
    li = Locale.objects.get_or_create(language_code="de-li")[0]
    fr = Locale.objects.get_or_create(language_code="fr-ch")[0]
    settings.WAGTAILLOCALIZE_MACHINE_TRANSLATOR = {
        **settings.WAGTAILLOCALIZE_MACHINE_TRANSLATOR,
        "OPTIONS": {"API_KEY": "test-key", "LANGUAGES": {"fr-ch": {"code": "fr-FR", "politeness": "more"}}},
    }
    html = admin.get("/admin/supertext/").content.decode()
    # es-es: the SDK's default is preselected; de-li has no default: "— choose —" and a warning.
    assert f'name="code-{es.pk}"' in html and '<option value="es" data-politeness="1" selected>' in html
    assert f'name="code-{li.pk}"' in html
    assert html.count("No Supertext language: choose one") == 1
    # fr-ch is set in the LANGUAGES option: no select, the option is shown.
    assert f'name="code-{fr.pk}"' not in html and "Set in the LANGUAGES option" in html


def test_status_page_saves_languages(admin):
    from wagtail_supertext.models import SupertextLanguage

    li = Locale.objects.get_or_create(language_code="de-li")[0]
    en = Locale.objects.get_or_create(language_code="en")[0]
    response = admin.post(
        "/admin/supertext/",
        {"action": "languages", f"code-{li.pk}": "de-CH", f"politeness-{li.pk}": "more",
         f"code-{en.pk}": "en-US", f"politeness-{en.pk}": "more"},
    )
    assert response.status_code == 302
    assert SupertextLanguage.objects.get(locale=li).code == "de-CH"
    assert SupertextLanguage.objects.get(locale=li).politeness == "more"
    # en-US offers no formal/informal: stored as the default tone.
    assert SupertextLanguage.objects.get(locale=en).politeness == "default"
    # A code that isn't in Supertext's list is refused.
    admin.post("/admin/supertext/", {"action": "languages", f"code-{li.pk}": "xx-YY"})
    assert SupertextLanguage.objects.get(locale=li).code == ""
