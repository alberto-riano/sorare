from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0018_alter_movementsnapshot_source_version"),
    ]

    operations = [
        migrations.AlterField(
            model_name="movementsnapshot",
            name="source_version",
            field=models.PositiveSmallIntegerField(default=10),
        ),
    ]
