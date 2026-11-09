"""
`python manage.py demo_setup` runs on every start of the demo container:

- creates the DEMO_ADMIN / DEMO_EDITOR accounts if missing (never changes existing ones)
- creates the locales (with the formal tone under Settings → Supertext, once), replaces Wagtail's welcome page with the demo home page once,
  and adds the English sample article once
- lets the editor group submit translations
"""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from wagtail.models import Locale, Page, Site
from wagtail_supertext.models import SupertextLanguage

from home.models import ArticlePage, HomePage

HOME_INTRO = (
    "<p>This site was written in <b>English</b>. Open any page in the admin and choose "
    "<i>Translate this page</i> to create the German, French and Italian versions with Supertext.</p>"
)
ARTICLE = {
    "title": "Swiss chocolate, shipped worldwide",
    "slug": "swiss-chocolate-shipped-worldwide",
    "intro": "<p>How a small family business in Bern brings handmade pralines to <b>40 countries</b>.</p>",
    "body": [
        ("heading", "From Bern to the world"),
        ("paragraph", '<p>Every praline is made by hand in our <b>Bern</b> workshop. Read more on <a href="https://www.supertext.com">our website</a>.</p>'),
        ("paragraph", "<ul><li>Fresh ingredients from local farmers</li><li>Climate-neutral delivery within 48 hours</li></ul>"),
        ("quote", {"text": "The best chocolate I have ever tasted.", "author": "A happy customer"}),
    ],
}


class Command(BaseCommand):
    help = "Creates the demo accounts, locales and sample content (idempotent)."

    def handle(self, *args, **options):
        self.ensure_accounts()
        self.ensure_locales()
        self.ensure_content()

    # ------------------------------------------------------------------- accounts

    def ensure_accounts(self):
        User = get_user_model()
        editors = Group.objects.filter(name="Moderators").first()
        if editors:
            # Wagtail's Moderators can edit and publish pages; translating needs this on top.
            editors.permissions.add(Permission.objects.get(codename="submit_translation", content_type__app_label="wagtail_localize"))

        for prefix, superuser in (("DEMO_ADMIN", True), ("DEMO_EDITOR", False)):
            email = os.environ.get(f"{prefix}_EMAIL", "")
            password = os.environ.get(f"{prefix}_PASSWORD", "")
            if not email or not password:
                self.stdout.write(f"{prefix}_EMAIL / {prefix}_PASSWORD not set; skipping that account.")
                continue
            if User.objects.filter(email__iexact=email).exists() or User.objects.filter(username=email).exists():
                self.stdout.write(f"{prefix}: account exists, left unchanged.")
                continue
            candidate = User(username=email, email=email)
            try:
                validate_password(password, candidate)
            except ValidationError as error:
                self.stderr.write(f"WARNING: {prefix}_PASSWORD does not meet Django's password rules ({' '.join(error.messages)}); account not created.")
                continue
            if superuser:
                User.objects.create_superuser(username=email, email=email, password=password, first_name="Demo", last_name="Admin")
            else:
                user = User.objects.create_user(username=email, email=email, password=password, first_name="Demo", last_name="Editor")
                if editors:
                    user.groups.add(editors)
            self.stdout.write(f"{prefix}: account created.")

    # -------------------------------------------------------------------- content

    def ensure_locales(self):
        for code, _name in settings.WAGTAIL_CONTENT_LANGUAGES:
            locale, _created = Locale.objects.get_or_create(language_code=code)
            if code != settings.LANGUAGE_CODE:
                # Settings → Supertext: the suggested language with the formal tone (Sie, vous, Lei),
                # only if nobody has chosen anything for this locale yet.
                SupertextLanguage.objects.get_or_create(locale=locale, defaults={"politeness": "more"})

    def ensure_content(self):
        en = Locale.objects.get(language_code="en")
        root = Page.get_first_root_node()
        home = HomePage.objects.filter(locale=en).first()
        if home is None:
            home = HomePage(title="Supertext Wagtail Demo", slug="home-en", locale=en, intro=HOME_INTRO)
            root.add_child(instance=home)
            home.save_revision().publish()
            site = Site.objects.filter(is_default_site=True).first() or Site(hostname="localhost", port=80, is_default_site=True)
            old_root = site.root_page_id
            site.root_page = home
            site.site_name = "Supertext Wagtail Demo"
            site.save()
            # Wagtail's own "Welcome to your new Wagtail site!" page
            Page.objects.filter(id=old_root, depth=2).exclude(id=home.id).delete()
            self.stdout.write("Home page created.")

        if not ArticlePage.objects.filter(locale=en, slug=ARTICLE["slug"]).exists():
            article = ArticlePage(locale=en, title=ARTICLE["title"], slug=ARTICLE["slug"], intro=ARTICLE["intro"], body=ARTICLE["body"])
            home.add_child(instance=article)
            article.save_revision().publish()
            self.stdout.write(f"Sample article added: {ARTICLE['title']}")
