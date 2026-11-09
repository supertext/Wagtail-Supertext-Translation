import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wagtail_supertext", "0001_initial"),
        ("wagtailcore", "0054_initial_locale"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupertextLanguage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(blank=True, default="", max_length=32)),
                (
                    "politeness",
                    models.CharField(
                        choices=[("default", "Default"), ("more", "Formal"), ("less", "Informal")],
                        default="default",
                        max_length=8,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "locale",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="supertext_language",
                        to="wagtailcore.locale",
                    ),
                ),
            ],
            options={"verbose_name": "Supertext language", "verbose_name_plural": "Supertext languages"},
        ),
    ]
