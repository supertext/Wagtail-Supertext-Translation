"""Translation review: locales with it on go through Supertext's review service, the others directly."""

import pytest
from django.contrib.auth import get_user_model
from wagtail.models import Locale
from wagtail_localize.strings import StringValue

from tests.fake import FakeSession
from wagtail_supertext import __version__, conf
from wagtail_supertext.client import LIVE, REVIEW_URL, user_agent
from wagtail_supertext.models import SupertextLanguage
from wagtail_supertext.translator import SupertextTranslator

pytestmark = pytest.mark.django_db

REVIEW = "https://review.test/v1/"


@pytest.fixture
def fake(monkeypatch):
    session = FakeSession(statuses=["running", "done"])

    def client(self, review=False):
        return self.config.client(review=review, session=session, sleep=lambda s: None)

    monkeypatch.setattr(SupertextTranslator, "client", client)
    for name in ("SUPERTEXT_API_KEY", "SUPERTEXT_API_ENDPOINT", "SUPERTEXT_REVIEW_URL"):
        monkeypatch.delenv(name, raising=False)
    return session


def calls(session):
    return [(c["method"], c["url"].split("/v1/")[0] + "/v1/", c["url"].split("/v1/")[1]) for c in session.calls]


def run(options, target):
    en = Locale.objects.get_or_create(language_code="en")[0]
    locale = Locale.objects.get_or_create(language_code=target)[0]
    return SupertextTranslator({"API_KEY": "test-key", **options}).translate(en, locale, [StringValue.from_plaintext("Hello")])


def test_review_locale_goes_through_the_review_service(fake):
    de = Locale.objects.get_or_create(language_code="de-ch")[0]
    SupertextLanguage.objects.create(locale=de, review=True)

    out = run({"REVIEW_URL": REVIEW}, "de-ch")
    assert list(out.values())[0].render_text() == "[de-CH] Hello"
    # All four calls (upload, status, download, delete) go to the review service, with the same key.
    assert calls(fake) == [
        ("POST", REVIEW, "translate/ai/file"),
        ("GET", REVIEW, "translate/ai/file/f1/status"),
        ("GET", REVIEW, "translate/ai/file/f1/status"),
        ("GET", REVIEW, "translate/ai/file/f1/translation"),
        ("DELETE", REVIEW, "translate/ai/file/f1"),
    ]
    assert all(c["headers"]["Authorization"] == "Supertext-Auth-Key test-key" for c in fake.calls)
    assert fake.calls[0]["headers"]["User-Agent"].endswith(f" Wagtail-Supertext-Translation/{__version__}")

    # Another locale (review off) goes directly to Supertext.
    fake.calls.clear()
    run({"REVIEW_URL": REVIEW}, "fr-ch")
    assert {base for _method, base, _path in calls(fake)} == {LIVE}


def test_empty_review_url_turns_review_off(fake):
    de = Locale.objects.get_or_create(language_code="de-ch")[0]
    SupertextLanguage.objects.create(locale=de, review=True)
    run({"REVIEW_URL": ""}, "de-ch")
    assert {base for _method, base, _path in calls(fake)} == {LIVE}


def test_review_url_from_the_environment(fake, monkeypatch):
    monkeypatch.setenv("SUPERTEXT_REVIEW_URL", REVIEW)
    run({"LANGUAGES": {"de-ch": {"review": True}}}, "de-ch")
    assert {base for _method, base, _path in calls(fake)} == {REVIEW}


def test_languages_option_wins_over_the_checkbox(fake):
    de = Locale.objects.get_or_create(language_code="de-ch")[0]
    SupertextLanguage.objects.create(locale=de, review=True)
    run({"REVIEW_URL": REVIEW, "LANGUAGES": {"de-ch": {"review": False}}}, "de-ch")
    assert {base for _method, base, _path in calls(fake)} == {LIVE}


def test_default_is_the_review_service_and_off_per_locale(monkeypatch):
    monkeypatch.delenv("SUPERTEXT_REVIEW_URL", raising=False)
    options = conf.load({"API_KEY": "k"}, saved={})
    assert options.review_url == REVIEW_URL == "https://localizationreview-production.up.railway.app/v1/"
    assert options.review("de-ch") is False  # off by default
    assert options.client().base_url == LIVE
    on = conf.load({"API_KEY": "k"}, saved={"de-ch": {"review": True}})
    assert on.review("de-CH") is True
    assert on.client(review=True).base_url == REVIEW_URL
    # The same URL as the API: no second client.
    same = conf.load({"API_KEY": "k", "REVIEW_URL": LIVE}, saved={"de-ch": {"review": True}})
    assert not same.review_available and same.review("de-ch") is False
    assert same.client(review=True).base_url == LIVE
    assert user_agent() == f"Wagtail-Supertext-Translation/{__version__}"


def test_key_check_never_uses_the_review_service(monkeypatch):
    from wagtail_supertext import reporting

    options = conf.load({"API_KEY": "k", "REVIEW_URL": REVIEW}, saved={"de-ch": {"review": True}})
    assert reporting.key_client(options, "k", None).base_url.startswith(LIVE.rstrip("/"))


def test_settings_page_saves_the_checkbox(client, monkeypatch):
    monkeypatch.delenv("SUPERTEXT_REVIEW_URL", raising=False)
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    de = Locale.objects.get_or_create(language_code="de-ch")[0]
    fr = Locale.objects.get_or_create(language_code="fr-ch")[0]
    client.force_login(user)
    html = client.get("/admin/supertext/").content.decode()
    assert "Translation review" in html and f'name="review-{de.pk}"' in html and REVIEW_URL in html
    client.post("/admin/supertext/", {"action": "languages", f"code-{de.pk}": "de-CH", f"review-{de.pk}": "1", f"code-{fr.pk}": "fr-CH"})
    assert SupertextLanguage.objects.get(locale=de).review is True
    assert SupertextLanguage.objects.get(locale=fr).review is False
    html = client.get("/admin/supertext/").content.decode()
    assert f'id="supertext-review-{de.pk}" aria-label="Translation review: ' in html and "checked" in html
