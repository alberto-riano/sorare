from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0035_sales_listing_refresh")]

    operations = [
        migrations.CreateModel(
            name="LineupInventory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cards", models.JSONField(default=list)),
                ("odds", models.JSONField(default=dict)),
                ("matches", models.JSONField(default=list)),
                ("refreshed_at", models.DateTimeField(blank=True, null=True)),
                ("odds_refreshed_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.OneToOneField(on_delete=models.deletion.CASCADE, related_name="sorare_lineup_inventory", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
