from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0028_alter_instantpurchasesnapshot_source_version"),
    ]

    operations = [
        migrations.AlterField(
            model_name="movementsnapshot",
            name="source_version",
            field=models.PositiveSmallIntegerField(default=15),
        ),
    ]
