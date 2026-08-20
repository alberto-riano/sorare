from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0014_alter_movementsnapshot_source_version"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicRewardSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("manager_slug", models.SlugField(max_length=180, unique=True)),
                ("manager_nickname", models.CharField(max_length=180)),
                ("movements", models.JSONField(default=list)),
                ("refreshed_at", models.DateTimeField(blank=True, null=True)),
                ("source_version", models.PositiveSmallIntegerField(default=1)),
            ],
        ),
        migrations.CreateModel(
            name="PublicRewardSyncJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("manager_slug", models.SlugField(db_index=True, max_length=180)),
                ("status", models.CharField(choices=[("queued", "En cola"), ("running", "Actualizando"), ("succeeded", "Completada"), ("failed", "Fallida")], db_index=True, default="queued", max_length=12)),
                ("movement_count", models.PositiveIntegerField(default=0)),
                ("processed_count", models.PositiveIntegerField(default=0)),
                ("progress_label", models.CharField(blank=True, max_length=180)),
                ("error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sorare_public_reward_sync_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("created_at",)},
        ),
    ]
