import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0004_auctionfilterpreset"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="SalesInventory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rarity", models.CharField(max_length=16, unique=True)),
                ("cards", models.JSONField(default=list)),
                ("refreshed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"verbose_name_plural": "sales inventories"},
        ),
        migrations.CreateModel(
            name="SalesRefreshJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("rarity", models.CharField(max_length=16)),
                ("status", models.CharField(choices=[("queued", "En cola"), ("running", "Actualizando"), ("succeeded", "Completada"), ("failed", "Fallida")], db_index=True, default="queued", max_length=12)),
                ("card_count", models.PositiveIntegerField(default=0)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sorare_sales_refresh_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("created_at",)},
        ),
        migrations.CreateModel(
            name="SaleBatchJob",
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
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sorare_sale_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("created_at",)},
        ),
        migrations.CreateModel(
            name="SaleBatchItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("position", models.PositiveSmallIntegerField()),
                ("asset_id", models.CharField(max_length=200)),
                ("player_name", models.CharField(max_length=180)),
                ("rarity", models.CharField(max_length=16)),
                ("euros", models.DecimalField(decimal_places=2, max_digits=8)),
                ("duration_days", models.PositiveSmallIntegerField(default=2)),
                ("status", models.CharField(choices=[("queued", "En cola"), ("running", "Procesando"), ("succeeded", "Completada"), ("failed", "Fallida")], default="queued", max_length=12)),
                ("error", models.TextField(blank=True)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="dashboard.salebatchjob")),
            ],
            options={"ordering": ("position",)},
        ),
        migrations.AddConstraint(model_name="salebatchitem", constraint=models.UniqueConstraint(fields=("job", "position"), name="unique_sale_job_position")),
    ]
