from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0022_movementpaymentevidence_and_snapshot_v13"),
    ]

    operations = [
        migrations.DeleteModel(name="MovementPaymentEvidence"),
        migrations.AlterField(
            model_name="movementsnapshot",
            name="source_version",
            field=models.PositiveSmallIntegerField(default=14),
        ),
    ]
