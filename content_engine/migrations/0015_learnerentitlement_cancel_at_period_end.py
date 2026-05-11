from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("content_engine", "0014_chatsession_is_archived"),
    ]

    operations = [
        migrations.AddField(
            model_name="learnerentitlement",
            name="cancel_at_period_end",
            field=models.BooleanField(
                default=False,
                help_text="User meminta berhenti di akhir pro_access_until; sampai saat itu paket tetap aktif.",
            ),
        ),
    ]
