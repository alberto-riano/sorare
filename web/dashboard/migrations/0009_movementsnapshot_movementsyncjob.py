from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dashboard", "0008_salebatchitem_minimum_offer_eur"),
    ]

    operations = [
        migrations.CreateModel(
            name="MovementSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("movements", models.JSONField(default=list)),
                ("refreshed_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="sorare_movement_snapshot", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="MovementSyncJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "En cola"), ("running", "Actualizando"), ("succeeded", "Completada"), ("failed", "Fallida")], db_index=True, default="queued", max_length=12)),
                ("movement_count", models.PositiveIntegerField(default=0)),
                ("processed_count", models.PositiveIntegerField(default=0)),
                ("progress_label", models.CharField(blank=True, max_length=180)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sorare_movement_sync_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("created_at",)},
        ),
    ]
