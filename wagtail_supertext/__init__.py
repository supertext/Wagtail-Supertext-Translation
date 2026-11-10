"""Supertext AI translation for Wagtail, as a wagtail-localize machine translator."""

__version__ = "1.0.0"


def __getattr__(name):
    # Imported lazily so Django settings can reference "wagtail_supertext.SupertextTranslator"
    # before the app registry is ready.
    if name == "SupertextTranslator":
        from .translator import SupertextTranslator

        return SupertextTranslator
    raise AttributeError(name)
