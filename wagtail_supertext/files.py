"""
"Export for translation" and "Import translation": a wagtail-localize translation as an XLIFF 1.2
or HTML file (the Supertext SDK's ``files``), translated outside the API, and the translated file
back into the translation.

An item is a wagtail-localize ``Translation`` (a source page or snippet in one target locale). The
segments are the strings Supertext's machine translation sends (``apply_machine_translation``:
the source's string segments that have no translation in the target locale yet), keyed
``<context path>#<string id>``. The import collects them again and writes the same way:
``StringTranslation`` rows (never replacing existing ones); the editor reviews and publishes in
wagtail-localize's editor as after a machine translation.

Reference (stored in the file): ``translation:<Translation id>:<target locale>``, e.g.
``translation:7:fr-ch``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from django.db import transaction
from django.utils.translation import gettext as _

from . import conf
from ._vendor.supertext import files as stfiles
from .client import SupertextError
from .translator import incoming, outgoing

logger = logging.getLogger(__name__)

#: The repository name, written into every file as the job's plugin.
PLUGIN = "Wagtail-Supertext-Translation"
#: Uploads larger than this are refused before they are read.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

XLIFF = "xliff"
HTML = "html"

_REFERENCE = re.compile(r"translation:([1-9][0-9]*):([A-Za-z0-9_-]{1,100})")


class FileError(Exception):
    """An export or import that can't go ahead; ``str(error)`` is the message in the admin language."""


@dataclass
class ExportedFile:
    name: str
    content_type: str
    content: str


@dataclass
class JobResult:
    """One job (item × target locale) of an imported file."""

    reference: str
    title: str = ""
    language: str = ""
    error: str = ""
    counts: dict[str, int] = field(default_factory=lambda: {"ok": 0, "changed": 0, "untranslated": 0, "invalid": 0, "missing": 0})


@dataclass
class _Segment:
    key: str
    string_id: int
    context_id: int
    text: str
    is_slug: bool


def reference(translation) -> str:
    return f"translation:{translation.pk}:{translation.target_locale.language_code}"


def parse_reference(value: str) -> tuple[int, str] | None:
    match = _REFERENCE.fullmatch(value or "")
    return (int(match.group(1)), match.group(2)) if match else None


def language_code(config: conf.Options, code: str, target: bool) -> str:
    """The Supertext code for a Wagtail locale (the mapping, the exact match, the SDK default); a
    language Supertext doesn't know keeps its own code with ``-``, so a file can still go to any
    translator."""
    try:
        return config.target_code(code) if target else config.source_code(code)
    except SupertextError:
        return code.replace("_", "-")


def check_size(size: int) -> None:
    if size > MAX_UPLOAD_BYTES:
        raise FileError(_("The file is too large (at most 10 MB)."))


def title(source) -> str:
    try:
        return str(source.get_source_instance())
    except Exception:  # noqa: BLE001 - the source object was deleted
        return str(source.object_id) if hasattr(source, "object_id") else str(source.pk)


def can_edit(user, translation) -> bool:
    """Whether the user may edit the translated page or snippet (wagtail-localize's own check)."""
    from wagtail_localize.views.edit_translation import user_can_edit_instance

    try:
        instance = translation.get_target_instance()
    except Exception:  # noqa: BLE001 - not created yet, or deleted
        return False
    return user_can_edit_instance(user, instance)


def collect(translation) -> list[_Segment]:
    """The strings a machine translation would send: the source's string segments without a
    translation in the target locale, once per string and context."""
    from wagtail_localize.models import StringTranslation

    done = set(
        StringTranslation.objects.filter(locale=translation.target_locale, translation_of__segments__source=translation.source)
        .values_list("translation_of_id", "context_id")
    )
    segments: dict[tuple[int, int], _Segment] = {}
    for string_segment in translation.source.stringsegment_set.all().select_related("context", "string").order_by("order"):
        pair = (string_segment.string_id, string_segment.context_id)
        if pair in done or pair in segments:
            continue
        text, is_slug = outgoing(string_segment.string.data)
        segments[pair] = _Segment(f"{string_segment.context.path}#{string_segment.string_id}", *pair, text, is_slug)
    return list(segments.values())


def _file_segments(segments: list[_Segment]) -> list[stfiles.FileSegment]:
    return [stfiles.FileSegment(segment.key, segment.text, html=not segment.is_slug) for segment in segments]


def export(translations: list, fmt: str, config: conf.Options | None = None) -> ExportedFile:
    """One XLIFF file (one job per translation) or one HTML file (exactly one translation) for
    translations of the same source."""
    config = config or conf.load()
    if not translations or (fmt == HTML and len(translations) != 1):
        raise FileError(_("Choose one language for an HTML file, or one or more for XLIFF."))
    jobs = []
    for translation in translations:
        segments = collect(translation)
        if not segments:
            continue
        jobs.append(stfiles.FileJob(
            PLUGIN,
            reference(translation),
            language_code(config, translation.source.locale.language_code, False),
            language_code(config, translation.target_locale.language_code, True),
            _file_segments(segments),
        ))
    if not jobs:
        raise FileError(_("Nothing to translate."))
    source = translations[0].source
    base = f"wagtail-{source.pk}"
    if len(jobs) == 1:
        base += "-" + re.sub(r"[^A-Za-z0-9-]+", "-", jobs[0].target_language)
    if fmt == HTML:
        return ExportedFile(base + ".html", "text/html; charset=utf-8", stfiles.export_html(jobs[0]))
    return ExportedFile(base + ".xlf", "application/x-xliff+xml", stfiles.export_xliff(jobs))


def import_file(content: bytes | str, *, user, include_changed: bool = False) -> list[JobResult]:
    """Imports a translated file: one result per job. Raises :class:`FileError` for a file that
    can't be read (too large, not XLIFF or HTML, no metadata)."""
    check_size(len(content))
    if isinstance(content, bytes):
        try:
            content = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise FileError(_("This file isn't a valid XLIFF or HTML translation file.")) from None
    try:
        files = stfiles.read(content)
    except stfiles.TranslationFileError as error:
        if error.code == "missing_metadata":
            raise FileError(_("This file wasn't exported by the Supertext plugin, or its header was removed.")) from None
        if error.code == "too_large":
            raise FileError(_("The file is too large (at most 10 MB).")) from None
        raise FileError(_("This file isn't a valid XLIFF or HTML translation file.")) from None
    return [_import_job(file, user, include_changed) for file in files]


def _import_job(file, user, include_changed: bool) -> JobResult:
    from wagtail.models import Locale
    from wagtail_localize.models import StringTranslation, Translation

    result = JobResult(file.reference, title=file.reference, language=file.target_language)
    if file.plugin != PLUGIN:
        result.error = _("This file belongs to another system (%(plugin)s).") % {"plugin": file.plugin}
        return result
    parsed = parse_reference(file.reference)
    translation = Translation.objects.filter(pk=parsed[0]).select_related("source", "target_locale").first() if parsed else None
    if translation is None:
        result.error = _("The page or snippet this file was exported from is no longer being translated.")
        return result
    code = parsed[1]
    locale = Locale.objects.filter(language_code=code).first()
    if locale is None or locale.pk != translation.target_locale_id:
        result.error = _("The site has no language %(language)s.") % {"language": code}
        return result
    result.title = title(translation.source)
    result.language = locale.get_display_name()
    if not can_edit(user, translation):
        result.error = _("You are not allowed to edit this page or snippet.")
        return result

    current = collect(translation)
    by_key = {segment.key: segment for segment in current}
    write: list[tuple[_Segment, str]] = []
    for imported in stfiles.import_translations(file, _file_segments(current)):
        if imported.status == "ok" or (imported.status == "source_changed" and include_changed):
            segment = by_key[imported.key]
            value = incoming(imported.text or "", segment.is_slug)
            if value is None:
                result.counts["invalid"] += 1
            else:
                write.append((segment, value.data))
        elif imported.status == "source_changed":
            result.counts["changed"] += 1
        elif imported.status == "untranslated":
            result.counts["untranslated"] += 1
        elif imported.status == "invalid_markup":
            result.counts["invalid"] += 1
        else:
            result.counts["missing"] += 1

    # Written exactly as wagtail-localize writes a machine translation: never over an existing one.
    with transaction.atomic():
        for segment, data in write:
            _row, created = StringTranslation.objects.get_or_create(
                translation_of_id=segment.string_id,
                locale=translation.target_locale,
                context_id=segment.context_id,
                defaults={
                    "data": data,
                    "translation_type": StringTranslation.TRANSLATION_TYPE_MANUAL,
                    "last_translated_by": user if getattr(user, "pk", None) else None,
                    "has_error": False,
                    "field_error": "",
                },
            )
            result.counts["ok"] += int(created)
    return result
