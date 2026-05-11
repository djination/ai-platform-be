from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("content_engine", "0012_billing_catalog_plan"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="title",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="chatsession",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
