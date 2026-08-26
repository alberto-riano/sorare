from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dashboard", "0024_auctionrefreshjob"),
    ]

    operations = [
        migrations.CreateModel(
            name="OpportunitySnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("market_key", models.CharField(default="laliga-2026", max_length=40, unique=True)),
                ("rows", models.JSONField(default=list)),
                ("metadata", models.JSONField(default=dict)),
                ("refreshed_at", models.DateTimeField(blank=True, null=True)),
                ("source_version", models.PositiveSmallIntegerField(default=1)),
            ],
        ),
        migrations.CreateModel(
            name="OpportunityRefreshJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "En cola"), ("running", "Analizando"), ("succeeded", "Completada"), ("failed", "Fallida")], db_index=True, default="queued", max_length=12)),
                ("processed_count", models.PositiveIntegerField(default=0)),
                ("total_count", models.PositiveIntegerField(default=0)),
                ("player_count", models.PositiveIntegerField(default=0)),
                ("opportunity_count", models.PositiveIntegerField(default=0)),
                ("progress_label", models.CharField(blank=True, max_length=180)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sorare_opportunity_refresh_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("created_at",)},
        ),
    ]
