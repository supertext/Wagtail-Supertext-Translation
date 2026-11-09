"""Settings → Supertext: shows the configuration and tests the API key."""

from __future__ import annotations

import os
import re

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET
from wagtail.models import Locale

from . import __version__, conf, files, reporting
from .client import API_KEY_URL, SIGNUP_URL, Languages
from .models import SupertextLanguage

RELEASES_URL = "https://github.com/Supertext/Wagtail-Supertext-Translation/releases/tag/v{version}"


def release_url(version: str) -> str | None:
    """Link to the GitHub release when the version looks like one (X.Y.Z)."""
    if re.fullmatch(r"\d+\.\d+\.\d+", version or ""):
        return RELEASES_URL.format(version=version)
    return None


def status(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    options = conf.load()
    if request.method == "POST" and request.POST.get("action") == "languages":
        save_languages(request, options)
        messages.success(request, _("The languages are saved."))
        reporting.in_background("settings_saved", conf.load(), request)
        return redirect("wagtail_supertext_status")
    if request.method == "POST":
        # Test connection: the SDK's key check (cost-free GET features), stored and reported if it changed.
        result = reporting.run("test_connection", options, request)
        check = result.check if result else None
        text = reporting.check_message(check) or _("The API key couldn't be checked because Supertext can't be reached. The settings are saved; check again later.")
        (messages.success if check and check.status == "ok" else messages.error)(request, text)
        return redirect("wagtail_supertext_status")

    reporting.in_background("on_request", options, request)

    config = getattr(settings, "WAGTAILLOCALIZE_MACHINE_TRANSLATOR", None) or {}
    locales = language_rows(options)

    check = reporting.last_check()
    env_reports = os.environ.get("SUPERTEXT_VERSION_REPORTS", "").strip()
    return TemplateResponse(
        request,
        "wagtail_supertext/status.html",
        {
            "page_title": _("Supertext"),
            "header_title": _("Supertext"),
            "header_icon": "globe",
            "active": config.get("CLASS") == conf.TRANSLATOR_CLASS,
            "options": options,
            "locales": locales,
            "target_options": [
                {"code": option.code, "label": f"{option.name} ({option.code})", "politeness": option.politeness}
                for option in Languages.target_options()
            ],
            "signup_url": SIGNUP_URL,
            "api_key_url": API_KEY_URL,
            "check": check,
            "check_message": reporting.check_message(check),
            "check_time": reporting.checked_at(check),
            "check_links": bool(check and check.status in ("invalid", "missing")),
            "reports_enabled": options.reports_enabled,
            "reports_source": "environment" if env_reports else ("settings" if options.version_reports is not None else ""),
            "ping_url": reporting.callback_url(reporting.site_url(request) or ""),
            "version": __version__,
            "version_url": release_url(__version__),
        },
    )


def language_rows(options: conf.Options) -> list[dict]:
    """One row per Wagtail locale (the default locale too: one mapping serves both directions)."""
    default = getattr(settings, "LANGUAGE_CODE", "")
    rows = []
    for locale in Locale.objects.order_by("language_code"):
        code = locale.language_code
        saved = conf._lookup(options.saved, code)
        override = options.override_code(code)
        selected = override or str(saved.get("code") or "") or Languages.suggest_target(code) or ""
        rows.append(
            {
                "id": locale.pk,
                "code": code,
                "name": locale.get_display_name(),
                "is_source": code.lower() == default.lower(),
                "selected": selected,
                "override": override,
                "politeness": options.politeness(code),
                "politeness_override": options.override_politeness(code),
                "politeness_supported": Languages.supports_politeness(selected) if selected else None,
                "review": options.review(code),
                "review_override": options.override_review(code),
                # Flagged only when there is neither a mapping nor a default: translating fails until chosen.
                "missing": not selected,
            }
        )
    return rows


def save_languages(request, options: conf.Options) -> None:
    """Stores the chosen Supertext code, tone and *Translation review* per locale. Only codes from Supertext's list are accepted;
    a locale set in the LANGUAGES option keeps its row untouched (the option wins anyway)."""
    valid = {option.code for option in Languages.target_options()}
    for locale in Locale.objects.all():
        if f"code-{locale.pk}" not in request.POST:
            continue
        code = request.POST.get(f"code-{locale.pk}", "").strip()
        if code and code not in valid:
            code = ""
        politeness = request.POST.get(f"politeness-{locale.pk}", "default")
        if politeness not in ("default", "more", "less") or not code or Languages.supports_politeness(code) is False:
            politeness = "default"
        row, _created = SupertextLanguage.objects.get_or_create(locale=locale)
        if not options.override_code(locale.language_code):
            row.code = code
        if not options.override_politeness(locale.language_code):
            row.politeness = politeness
        if options.override_review(locale.language_code) is None:
            row.review = request.POST.get(f"review-{locale.pk}") == "1"
        row.save()


#: How many translated pages/snippets the export list offers (the most recently updated).
EXPORT_SOURCES = 200


def translation_files(request):
    """Settings → Translation files: Export for translation and Import translation (XLIFF, HTML).

    Any admin user may open it; the export lists, and the import writes, only translations of pages
    and snippets the user may edit (wagtail-localize's own check)."""
    from wagtail_localize.models import Translation, TranslationSource

    sources = []
    for source in TranslationSource.objects.filter(translations__isnull=False).distinct().select_related("locale").order_by("-last_updated_at")[:EXPORT_SOURCES]:
        if any(files.can_edit(request.user, t) for t in source.translations.select_related("target_locale")):
            sources.append({"id": source.pk, "label": f"{files.title(source)} ({source.locale.get_display_name()})"})

    selected = request.POST.get("source") or request.GET.get("source") or ""
    source = TranslationSource.objects.filter(pk=selected).select_related("locale").first() if selected.isdigit() else None
    if source is not None and not any(item["id"] == source.pk for item in sources):
        source = None
    targets = []
    if source is not None:
        targets = [t for t in source.translations.select_related("target_locale").order_by("target_locale__language_code") if files.can_edit(request.user, t)]

    export_error = import_error = ""
    import_results = None
    fmt = request.POST.get("format") if request.POST.get("format") in (files.XLIFF, files.HTML) else files.XLIFF
    chosen = {int(v) for v in request.POST.getlist("targets") if v.isdigit()}

    if request.method == "POST" and request.POST.get("action") == "export":
        picked = [t for t in targets if t.pk in chosen]
        if source is None:
            export_error = _("The page or snippet this file was exported from is no longer being translated.")
        else:
            try:
                exported = files.export(picked, fmt)
            except files.FileError as error:
                export_error = str(error)
            else:
                response = HttpResponse(exported.content, content_type=exported.content_type)
                response["Content-Disposition"] = f'attachment; filename="{exported.name}"'
                response["X-Content-Type-Options"] = "nosniff"
                response["Cache-Control"] = "no-store"
                return response
    elif request.method == "POST" and request.POST.get("action") == "import":
        upload = request.FILES.get("file")
        if upload is None:
            import_error = _("This file isn't a valid XLIFF or HTML translation file.")
        else:
            try:
                files.check_size(upload.size)  # before anything is read
                results = files.import_file(upload.read(), user=request.user, include_changed=request.POST.get("include_changed") == "1")
            except files.FileError as error:
                import_error = str(error)
            else:
                import_results = [_import_lines(result) for result in results]

    return TemplateResponse(
        request,
        "wagtail_supertext/files.html",
        {
            "page_title": _("Translation files"),
            "header_title": _("Translation files"),
            "header_icon": "doc-full",
            "sources": sources,
            "source": source,
            "targets": [{"id": t.pk, "name": t.target_locale.get_display_name(), "code": t.target_locale.language_code} for t in targets],
            "chosen": chosen,
            "format": fmt,
            "export_error": export_error,
            "import_error": import_error,
            "import_results": import_results,
            "max_upload": files.MAX_UPLOAD_BYTES,
        },
    )


def _import_lines(result: files.JobResult) -> dict:
    counts = result.counts
    lines = []
    if not result.error:
        lines.append(_("Imported: %(count)s") % {"count": counts["ok"]})
        for key, text in (
            ("changed", _("Skipped, source changed since the export: %(count)s")),
            ("untranslated", _("Not translated in the file: %(count)s")),
            ("invalid", _("Formatting changed, not imported: %(count)s")),
            ("missing", _("Not in the file: %(count)s")),
        ):
            if counts[key]:
                lines.append(text % {"count": counts[key]})
    return {"title": result.title, "language": result.language, "error": result.error, "lines": lines}


@require_GET
def ping(request):
    """Public ping endpoint for the Supertext Commando Center (no login, no session, never cached).

    Answers exactly what the SDK's ``ping_response`` returns: 200 with the challenge echoed,
    400 for a bad challenge, 404 while version reports are off.
    """
    status, headers, body = reporting.ping(request.GET.get("challenge"))
    response = HttpResponse(body, status=status)
    for name, value in headers.items():
        response[name] = value
    request._cache_update_cache = False  # never stored by Django's cache middleware
    return response
