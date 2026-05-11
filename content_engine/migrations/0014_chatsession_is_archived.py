from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("content_engine", "0013_chatsession_title_deleted_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="is_archived",
            field=models.BooleanField(default=False),
        ),
    ]
