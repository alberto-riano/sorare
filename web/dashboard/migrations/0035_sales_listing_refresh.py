from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0034_delist_batch_jobs")]

    operations = [
        migrations.AddField(
            model_name="salesinventory",
            name="listings_refreshed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="salesrefreshjob",
            name="mode",
            field=models.CharField(
                choices=[("full", "Inventario completo"), ("listings", "Sólo publicaciones")],
                db_index=True,
                default="full",
                max_length=12,
            ),
        ),
    ]
