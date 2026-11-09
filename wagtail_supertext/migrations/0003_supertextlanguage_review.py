from django.db import migrations, models


class Migration(migrations.Migration):
    """*Translation review* per locale (Settings → Supertext → Languages); existing locales start with it off."""

    dependencies = [
        ("wagtail_supertext", "0002_supertextlanguage"),
    ]

    operations = [
        migrations.AddField(
            model_name="supertextlanguage",
            name="review",
            field=models.BooleanField(default=False),
        ),
    ]
