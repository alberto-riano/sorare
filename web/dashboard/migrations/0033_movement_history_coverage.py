from datetime import date

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0032_bidbatchitem_market_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="movementsnapshot",
            name="history_start_date",
            field=models.DateField(default=date(2026, 8, 12)),
        ),
        migrations.AddField(
            model_name="movementsyncjob",
            name="requested_start_date",
            field=models.DateField(default=date(2026, 8, 12)),
        ),
        migrations.AddField(
            model_name="publicrewardsnapshot",
            name="history_start_date",
            field=models.DateField(default=date(2026, 8, 12)),
        ),
        migrations.AddField(
            model_name="publicrewardsyncjob",
            name="requested_start_date",
            field=models.DateField(default=date(2026, 8, 12)),
        ),
    ]
