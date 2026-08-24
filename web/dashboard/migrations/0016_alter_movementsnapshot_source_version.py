from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0015_publicrewardsnapshot_publicrewardsyncjob"),
    ]

    operations = [
        migrations.AlterField(
            model_name="movementsnapshot",
            name="source_version",
            field=models.PositiveSmallIntegerField(default=7),
        ),
    ]
