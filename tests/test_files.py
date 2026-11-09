"""Export for translation / Import translation (XLIFF and HTML) with wagtail-localize, on SQLite."""

import re

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from wagtail.models import Locale
from wagtail_localize.models import StringTranslation, Translation, TranslationSource

from tests.test_translator import page  # noqa: F401 (fixture)
from wagtail_supertext import conf, files
from wagtail_supertext._vendor.supertext import files as stfiles

pytestmark = pytest.mark.django_db

CODE = re.compile(r"(<(bpt|ept|ph)\b[^>]*>.*?</\2>)", re.S)


@pytest.fixture
def admin():
    return get_user_model().objects.create_superuser("admin", "admin@example.com", "pw")


def start(page, code, user):  # noqa: F811 (page: the article)
    source, _ = TranslationSource.get_or_create_from_instance(page)
    translation = Translation.objects.create(source=source, target_locale=Locale.objects.get(language_code=code))
    translation.save_target(user=user)
    return translation


def translate_xliff(xliff: str, prefix: str = "[FR] ", skip=()) -> str:
    """A <target> for every unit: the source with its inline codes, the words prefixed."""

    def unit(match):
        if any(match.group(2).startswith(s) for s in skip):
            return match.group(0)
        parts = CODE.split(match.group(3))
        target, i = "", 0
        while i < len(parts):
            target += (prefix + parts[i]) if parts[i].strip() else parts[i]
            if i + 1 < len(parts):
                target += parts[i + 1]
            i += 3
        return f"{match.group(1)}<source>{match.group(3)}</source><target>{target}</target>"

    return re.sub(r'(<trans-unit id="([^"]+)"[^>]*>\s*)<source>(.*?)</source>', unit, xliff, flags=re.S)


def options(**languages):
    return conf.load({"API_KEY": "k", "LANGUAGES": languages})


def test_reference_round_trip(page, admin):  # noqa: F811
    translation = start(page, "fr-ch", admin)
    assert files.reference(translation) == f"translation:{translation.pk}:fr-ch"
    assert files.parse_reference(f"translation:{translation.pk}:fr-ch") == (translation.pk, "fr-ch")
    assert files.parse_reference("translation:0:fr-ch") is None
    assert files.parse_reference("page:3:fr-ch") is None


def test_language_codes_use_the_mapping_and_fall_back():
    config = options(**{"fr-ch": {"code": "fr-FR"}})
    assert files.language_code(config, "fr-ch", True) == "fr-FR"
    assert files.language_code(config, "fr-ch", False) == "fr"
    assert files.language_code(config, "en_IE", True) == "en-IE"


def test_export_xliff_has_the_machine_translations_strings(page, admin):  # noqa: F811
    fr, de = start(page, "fr-ch", admin), start(page, "de-ch", admin)
    exported = files.export([de, fr], files.XLIFF, options())

    assert exported.name == f"wagtail-{fr.source.pk}.xlf" and exported.content_type == "application/x-xliff+xml"
    jobs = stfiles.read(exported.content)
    assert [(j.plugin, j.reference, j.source_language, j.target_language) for j in jobs] == [
        (files.PLUGIN, f"translation:{de.pk}:de-ch", "en", "de-CH"),
        (files.PLUGIN, f"translation:{fr.pk}:fr-ch", "en", "fr-CH"),
    ]
    keys = [u.key for u in jobs[0].units]
    assert keys == [s.key for s in files.collect(de)] and any(k.startswith("title#") for k in keys)
    assert all("#" in k for k in keys)


def test_export_html_needs_one_language_and_text(page, admin):  # noqa: F811
    fr, de = start(page, "fr-ch", admin), start(page, "de-ch", admin)
    exported = files.export([fr], files.HTML, options())
    assert exported.name == f"wagtail-{fr.source.pk}-fr-CH.html" and "data-st-id" in exported.content
    with pytest.raises(files.FileError, match="HTML file"):
        files.export([fr, de], files.HTML, options())

    # Everything translated already: the machine translation would send nothing either.
    for segment in files.collect(fr):
        StringTranslation.objects.create(translation_of_id=segment.string_id, locale=fr.target_locale, context_id=segment.context_id, data="x")
    with pytest.raises(files.FileError, match="Nothing to translate"):
        files.export([fr], files.XLIFF, options())


def test_import_writes_string_translations(page, admin):  # noqa: F811
    fr = start(page, "fr-ch", admin)
    count = len(files.collect(fr))
    xliff = translate_xliff(files.export([fr], files.XLIFF, options()).content)

    [result] = files.import_file(xliff.encode(), user=admin)

    assert result.error == "" and result.title == "Swiss chocolate" and result.language == "Français (Suisse)"
    assert result.counts == {"ok": count, "changed": 0, "untranslated": 0, "invalid": 0, "missing": 0}
    rows = StringTranslation.objects.filter(locale=fr.target_locale)
    assert rows.count() == count and all(r.translation_type == "manual" for r in rows)
    title = rows.get(translation_of__data="Swiss chocolate")
    assert title.data == "[FR] Swiss chocolate"
    slug = rows.get(translation_of__data="swiss-chocolate-shipped-worldwide")
    assert slug.data == "fr-swiss-chocolate-shipped-worldwide"  # sent as words, slugified again
    assert files.collect(fr) == []  # nothing left: a second import writes nothing
    fr.save_target(user=admin, publish=True)
    assert page.get_translation(fr.target_locale).title == "[FR] Swiss chocolate"


def test_source_changed_only_with_the_option(page, admin):  # noqa: F811
    fr = start(page, "fr-ch", admin)
    xliff = translate_xliff(files.export([fr], files.XLIFF, options()).content)
    # The checksum of the title in the file no longer matches (as if the source had been edited).
    xliff, n = re.subn(r'(<trans-unit id="title#[^"]+"[^>]*?extradata=")[0-9a-f]{8}', r"\g<1>00000000", xliff)
    assert n == 1

    [result] = files.import_file(xliff, user=admin)
    assert result.counts["changed"] == 1
    assert not StringTranslation.objects.filter(locale=fr.target_locale, translation_of__data="Swiss chocolate").exists()

    [result] = files.import_file(xliff, user=admin, include_changed=True)
    assert result.counts["changed"] == 0 and result.counts["ok"] == 1
    assert StringTranslation.objects.get(locale=fr.target_locale, translation_of__data="Swiss chocolate").data == "[FR] Swiss chocolate"


def test_untranslated_invalid_and_missing_are_not_written(page, admin):  # noqa: F811
    fr = start(page, "fr-ch", admin)
    xliff = files.export([fr], files.XLIFF, options()).content
    xliff = translate_xliff(xliff, skip=("title#",))  # untranslated
    xliff = re.sub(r'<trans-unit id="slug#.*?</trans-unit>', "", xliff, flags=re.S)  # missing
    xliff = re.sub(r"(<target>)(.*?)(</target>)", lambda m: m.group(1) + m.group(2) + '<ph id="99">&lt;br&gt;</ph>' + m.group(3), xliff, flags=re.S)  # invalid

    [result] = files.import_file(xliff, user=admin)

    assert result.counts["ok"] == 0 and result.counts["untranslated"] == 1 and result.counts["missing"] == 1
    assert result.counts["invalid"] == len(files.collect(fr)) - 2
    assert not StringTranslation.objects.filter(locale=fr.target_locale).exists()


def test_errors_per_job(page, admin):  # noqa: F811
    fr = start(page, "fr-ch", admin)
    job = lambda ref, plugin=files.PLUGIN: stfiles.export_xliff([stfiles.FileJob(plugin, ref, "en", "fr-CH", [stfiles.FileSegment("title#1", "Hi")])])  # noqa: E731

    assert files.import_file(job("x", "Contao-Supertext-Translation"), user=admin)[0].error == "This file belongs to another system (Contao-Supertext-Translation)."
    assert files.import_file(job("page:1"), user=admin)[0].error.startswith("The page or snippet this file was exported from")
    assert files.import_file(job("translation:999:fr-ch"), user=admin)[0].error.startswith("The page or snippet this file was exported from")
    assert files.import_file(job(f"translation:{fr.pk}:it"), user=admin)[0].error == "The site has no language it."
    nobody = get_user_model().objects.create_user("nobody", "nobody@example.com", "pw")
    assert files.import_file(job(f"translation:{fr.pk}:fr-ch"), user=nobody)[0].error == "You are not allowed to edit this page or snippet."
    assert not StringTranslation.objects.filter(locale=fr.target_locale).exists()


def test_invalid_files_and_size():
    with pytest.raises(files.FileError, match="exported by the Supertext plugin"):
        files.import_file("<html><head><title>x</title></head><body><p>x</p></body></html>", user=None)
    with pytest.raises(files.FileError, match="valid XLIFF or HTML"):
        files.import_file('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x/>', user=None)
    files.check_size(files.MAX_UPLOAD_BYTES)
    with pytest.raises(files.FileError, match="too large"):
        files.check_size(files.MAX_UPLOAD_BYTES + 1)


def test_files_page_export_and_import(page, admin, client):  # noqa: F811
    fr = start(page, "fr-ch", admin)
    client.force_login(admin)
    html = client.get("/admin/supertext/files/").content.decode()
    assert "Export for translation" in html and "Swiss chocolate (English)" in html

    html = client.get(f"/admin/supertext/files/?source={fr.source.pk}").content.decode()
    assert f'value="{fr.pk}"' in html

    response = client.post("/admin/supertext/files/", {"action": "export", "source": fr.source.pk, "targets": [fr.pk], "format": "xliff"})
    assert response["Content-Type"] == "application/x-xliff+xml"
    assert response["Content-Disposition"] == f'attachment; filename="wagtail-{fr.source.pk}-fr-CH.xlf"'

    upload = SimpleUploadedFile("fr.xlf", translate_xliff(response.content.decode()).encode())
    html = client.post("/admin/supertext/files/", {"action": "import", "file": upload}).content.decode()
    assert "Imported: " in html and "Swiss chocolate → Français (Suisse)" in html
    assert StringTranslation.objects.filter(locale=fr.target_locale).exists()


def test_files_page_lists_only_editable_translations(page, admin, client):  # noqa: F811
    start(page, "fr-ch", admin)
    user = get_user_model().objects.create_user("editor", "editor@example.com", "pw")
    from django.contrib.auth.models import Permission

    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    client.force_login(user)
    html = client.get("/admin/supertext/files/").content.decode()
    assert "Swiss chocolate (English)" not in html
