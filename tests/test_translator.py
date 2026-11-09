"""wagtail-localize end to end: submit a page for translation, machine-translate it, publish."""

import pytest
from django.contrib.auth import get_user_model
from wagtail.models import Locale, Page
from wagtail_localize.machine_translators import get_machine_translator
from wagtail_localize.models import Translation, TranslationSource
from wagtail_localize.strings import StringValue
from wagtail_localize.views.edit_translation import apply_machine_translation

from tests.fake import FakeSession
from tests.testapp.models import ArticlePage

pytestmark = pytest.mark.django_db


@pytest.fixture
def fake(monkeypatch):
    session = FakeSession()
    from wagtail_supertext.translator import SupertextTranslator

    def client(self, review=False):
        return self.config.client(review=review, session=session, sleep=lambda s: None)

    monkeypatch.setattr(SupertextTranslator, "client", client)
    monkeypatch.delenv("SUPERTEXT_API_KEY", raising=False)
    monkeypatch.delenv("SUPERTEXT_API_ENDPOINT", raising=False)
    return session


@pytest.fixture
def page():
    en = Locale.objects.get_or_create(language_code="en")[0]
    Locale.objects.get_or_create(language_code="de-ch")
    Locale.objects.get_or_create(language_code="fr-ch")
    root = Page.get_first_root_node()
    article = ArticlePage(
        title="Swiss chocolate",
        slug="swiss-chocolate-shipped-worldwide",
        locale=en,
        intro='<p>Made by hand in <b>Bern</b>. <a href="https://www.supertext.com">Read more</a>.</p>',
        body=[("heading", "From Bern to the world"), ("paragraph", "<p>Fresh <i>local</i> ingredients.</p>")],
    )
    root.add_child(instance=article)
    return article


def translate(page, code, user):
    source, _ = TranslationSource.get_or_create_from_instance(page)
    translation = Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code=code))
    translation.save_target(user=user)  # creates the (draft) translated page
    applied = apply_machine_translation(translation.id, user, get_machine_translator())
    translation.save_target(user=user, publish=True)
    return applied, page.get_translation(Locale.objects.get(language_code=code)).specific


def test_translator_is_configured():
    translator = get_machine_translator()
    assert translator.display_name == "Supertext"


def test_translates_a_page(fake, page):
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    applied, german = translate(page, "de-ch", user)

    assert applied
    assert german.title == "[de-CH] Swiss chocolate"
    assert german.slug == "de-ch-swiss-chocolate-shipped-worldwide"  # translated as words, then slugified
    # The link URL and formatting survive; the whole paragraph went to Supertext as one string.
    assert '<a href="https://www.supertext.com">[de-CH] Read more</a>' in german.intro
    assert "<b>[de-CH] Bern</b>" in german.intro
    assert german.body[0].value == "[de-CH] From Bern to the world"
    assert "<i>[de-CH] local</i>" in german.body[1].value.source

    # One document per target language, every string in its own data-st-id element
    post = fake.calls[0]
    assert post["data"]["target_lang"] == "de-CH" and post["data"]["source_lang"] == "en"
    html = post["files"]["file"][1].decode()
    assert '<div data-st-id="0">' in html and 'href' not in html  # wagtail-localize strips attributes first


def test_politeness_from_language_settings(fake, page):
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    translate(page, "fr-ch", user)
    assert fake.calls[0]["data"] == {"target_lang": "fr-CH", "source_lang": "en", "politeness": "more"}


def test_nothing_left_to_translate(fake, page):
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    translate(page, "de-ch", user)
    translation = Translation.objects.get(target_locale__language_code="de-ch")
    assert apply_machine_translation(translation.id, user, get_machine_translator()) is False


def test_errors_become_a_message_in_the_editor(monkeypatch, page, client):
    session = FakeSession(statuses=["limit_exceeded"])
    from wagtail_supertext.translator import SupertextTranslator

    def fake_client(self, review=False):
        return self.config.client(review=review, session=session, sleep=lambda s: None)

    monkeypatch.setattr(SupertextTranslator, "client", fake_client)
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    source, _ = TranslationSource.get_or_create_from_instance(page)
    translation = Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code="de-ch"))
    translation.save_target(user=user)
    client.force_login(user)
    response = client.post(f"/admin/localize/translate/{translation.id}/machine_translate/?next=/admin/", follow=True)
    assert response.status_code == 200
    assert any("limit is exceeded" in str(m) for m in response.context["messages"])


def test_status_page(client, monkeypatch):
    monkeypatch.delenv("SUPERTEXT_API_KEY", raising=False)
    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    Locale.objects.get_or_create(language_code="fr-ch")
    client.force_login(user)
    response = client.get("/admin/supertext/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Set (Django settings)" in html and "https://api.supertext.com/v1/" in html and "fr-CH" in html


def test_status_page_shows_version(client):
    from wagtail_supertext import __version__
    from wagtail_supertext.views import release_url

    user = get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")
    client.force_login(user)
    html = client.get("/admin/supertext/").content.decode()
    assert "Plugin version" in html and __version__ in html
    if release_url(__version__):
        assert f'href="https://github.com/Supertext/Wagtail-Supertext-Translation/releases/tag/v{__version__}"' in html
    assert release_url("1.2.3").endswith("/releases/tag/v1.2.3")
    assert release_url("1.2.3.dev0") is None


def test_language_without_default_is_not_sent(fake, page, settings):
    """A locale without mapping and without SDK default: a message, and nothing goes to Supertext."""
    from wagtail_supertext.client import SupertextError
    from wagtail_supertext.models import SupertextLanguage
    from wagtail_supertext.translator import SupertextTranslator

    translator = SupertextTranslator({"API_KEY": "test-key"})
    en, li = Locale.objects.get(language_code="en"), Locale.objects.create(language_code="de-li")
    assert translator.can_translate(en, li)  # the button stays, so the editor gets the message
    with pytest.raises(SupertextError, match="The language de-li has no Supertext language yet") as info:
        translator.translate(en, li, [StringValue.from_plaintext("Hello")])
    assert info.value.code == "unsupported_language"
    assert "Settings → Supertext → Languages" in str(info.value)
    assert fake.calls == []

    # Chosen under Settings → Supertext: read from the database.
    SupertextLanguage.objects.create(locale=li, code="de-CH", politeness="more")
    out = SupertextTranslator({"API_KEY": "test-key"}).translate(en, li, [StringValue.from_plaintext("Hello")])
    assert fake.calls[0]["data"]["target_lang"] == "de-CH"
    assert fake.calls[0]["data"]["politeness"] == "more"
    assert list(out.values())[0].render_text() == "[de-CH] Hello"


def test_language_with_default_is_translated(fake, page):
    """es-es has no exact match but a default (es): sent without any mapping."""
    from wagtail_supertext.translator import SupertextTranslator

    en, es = Locale.objects.get(language_code="en"), Locale(language_code="es-es")
    out = SupertextTranslator({"API_KEY": "test-key"}).translate(en, es, [StringValue.from_plaintext("Hello")])
    assert fake.calls[0]["data"]["target_lang"] == "es"
    assert list(out.values())[0].render_text() == "[es] Hello"


def test_same_language_cannot_translate():
    from wagtail_supertext.translator import SupertextTranslator

    translator = SupertextTranslator({"API_KEY": "test-key"})
    assert not translator.can_translate(Locale(language_code="de-ch"), Locale(language_code="de-ch"))
    assert translator.can_translate(Locale(language_code="en"), Locale(language_code="de-ch"))


def test_politeness_for_a_target_without_it_is_not_dropped(fake):
    from wagtail_supertext.client import SupertextError
    from wagtail_supertext.translator import SupertextTranslator

    translator = SupertextTranslator({"API_KEY": "test-key", "LANGUAGES": {"en-us": {"politeness": "more"}}})
    with pytest.raises(SupertextError, match="no formal or informal tone") as info:
        translator.translate(Locale(language_code="de-ch"), Locale(language_code="en-us"), [StringValue.from_plaintext("Hallo")])
    assert info.value.code == "unsupported_feature"
    assert fake.calls == []


def test_rejected_key_during_translation_is_reported(fake, page, center, monkeypatch):
    from wagtail_supertext.client import SupertextError
    from wagtail_supertext.translator import SupertextTranslator

    translator = SupertextTranslator({"API_KEY": "wrong"})
    monkeypatch.setattr(center, "key", "wrong")  # the key check would accept it; the translation is rejected
    with pytest.raises(SupertextError) as info:
        translator.translate(Locale.objects.get(language_code="en"), Locale.objects.get(language_code="de-ch"), [StringValue.from_plaintext("Hi")])
    assert info.value.code == "authentication_failure"
    assert center.reports[-1]["event"] in ("installed", "api_key_check")
    assert center.reports[-1]["api_key_check"]["status"] == "invalid"
