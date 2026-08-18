from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0005_sales_inventory_and_jobs")]

    operations = [
        migrations.AddField(
            model_name="salesrefreshjob",
            name="processed_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="salesrefreshjob",
            name="total_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="salesrefreshjob",
            name="progress_label",
            field=models.CharField(blank=True, max_length=180),
        ),
    ]
