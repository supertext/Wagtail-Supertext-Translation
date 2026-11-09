from django.db import models


class SupertextState(models.Model):
    """Small key/value store of the plugin: the version reporter's JSON state (``key = "reporter"``).

    Never holds the API key: the key check keeps only a SHA-256 fingerprint of it.
    """

    key = models.CharField(max_length=64, primary_key=True)
    value = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Supertext state"
        verbose_name_plural = "Supertext state"

    def __str__(self):
        return self.key


class SupertextLanguage(models.Model):
    """The Supertext language of a Wagtail locale, chosen under *Settings → Supertext → Languages*.

    One mapping for both directions: ``code`` is the Supertext target code (one of the SDK's
    ``Languages.target_options()``); as source it is sent in its source form (``de-CH`` → ``de``).
    An empty ``code`` means "not chosen": the SDK's default applies (``de`` → ``de-DE``), if there is one.
    ``review`` (*Translation review*): translations into this locale go through Supertext's review
    service (the ``REVIEW_URL`` option) instead of directly to Supertext. Off by default.
    The ``LANGUAGES`` option in the Django settings wins over these rows.
    """

    POLITENESS_CHOICES = [("default", "Default"), ("more", "Formal"), ("less", "Informal")]

    locale = models.OneToOneField("wagtailcore.Locale", on_delete=models.CASCADE, related_name="supertext_language")
    code = models.CharField(max_length=32, blank=True, default="")
    politeness = models.CharField(max_length=8, choices=POLITENESS_CHOICES, default="default")
    review = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Supertext language"
        verbose_name_plural = "Supertext languages"

    def __str__(self):
        return f"{self.locale_id}: {self.code or '-'}"
