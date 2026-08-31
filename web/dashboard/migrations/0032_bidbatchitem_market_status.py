from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0031_alter_movementsnapshot_source_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="bidbatchitem",
            name="market_status",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
