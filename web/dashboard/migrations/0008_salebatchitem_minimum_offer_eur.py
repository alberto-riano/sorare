from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0007_alter_salebatchitem_duration_days"),
    ]

    operations = [
        migrations.AddField(
            model_name="salebatchitem",
            name="minimum_offer_eur",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
    ]
