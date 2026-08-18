from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0006_salesrefreshjob_progress")]

    operations = [
        migrations.AlterField(
            model_name="salebatchitem",
            name="duration_days",
            field=models.PositiveSmallIntegerField(default=7),
        ),
    ]
