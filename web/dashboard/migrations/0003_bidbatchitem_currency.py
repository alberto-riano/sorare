from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0002_bidbatchjob_bidbatchitem")]
    operations = [
        migrations.AddField(
            model_name="bidbatchitem",
            name="currency",
            field=models.CharField(choices=[("EUR", "EUR"), ("ETH", "ETH")], default="EUR", max_length=3),
        ),
    ]
