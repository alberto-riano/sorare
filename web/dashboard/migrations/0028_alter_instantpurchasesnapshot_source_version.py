from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0027_instant_purchase_market"),
    ]

    operations = [
        migrations.AlterField(
            model_name="instantpurchasesnapshot",
            name="source_version",
            field=models.PositiveSmallIntegerField(default=2),
        ),
    ]