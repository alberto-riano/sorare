import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0033_movement_history_coverage"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DelistBatchJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_key", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("status", models.CharField(choices=[("queued", "En cola"), ("running", "Procesando"), ("succeeded", "Completada"), ("partial", "Parcial"), ("failed", "Fallida")], db_index=True, default="queued", max_length=12)),
                ("total_count", models.PositiveSmallIntegerField(default=0)),
                ("success_count", models.PositiveSmallIntegerField(default=0)),
                ("failure_count", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sorare_delist_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("created_at",)},
        ),
        migrations.CreateModel(
            name="DelistBatchItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveSmallIntegerField()),
                ("asset_id", models.CharField(max_length=200)),
                ("offer_id", models.CharField(max_length=200)),
                ("player_name", models.CharField(max_length=180)),
                ("rarity", models.CharField(max_length=16)),
                ("status", models.CharField(choices=[("queued", "En cola"), ("running", "Procesando"), ("succeeded", "Completada"), ("failed", "Fallida")], default="queued", max_length=12)),
                ("error", models.TextField(blank=True)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="dashboard.delistbatchjob")),
            ],
            options={"ordering": ("position",)},
        ),
        migrations.AddConstraint(
            model_name="delistbatchitem",
            constraint=models.UniqueConstraint(fields=("job", "position"), name="unique_delist_job_position"),
        ),
    ]
