from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0036_lineup_inventory")]

    operations = [
        migrations.CreateModel(
            name="LineupRefreshJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "En cola"), ("running", "Actualizando"), ("succeeded", "Completada"), ("failed", "Fallida")], db_index=True, default="queued", max_length=12)),
                ("processed_count", models.PositiveIntegerField(default=0)),
                ("total_count", models.PositiveIntegerField(default=0)),
                ("card_count", models.PositiveIntegerField(default=0)),
                ("progress_label", models.CharField(blank=True, max_length=180)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="sorare_lineup_refresh_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("created_at",)},
        ),
    ]
