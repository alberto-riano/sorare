from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0009_movementsnapshot_movementsyncjob")]

    operations = [
        migrations.AddField(
            model_name="movementsnapshot",
            name="source_version",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AlterField(
            model_name="movementsnapshot",
            name="source_version",
            field=models.PositiveSmallIntegerField(default=2),
        ),
    ]
