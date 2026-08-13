import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dashboard", "0003_bidbatchitem_currency"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(
            name="AuctionFilterPreset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=60)),
                ("query_string", models.CharField(max_length=1500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sorare_auction_filters", to=settings.AUTH_USER_MODEL)),
            ], options={"ordering": ("name",)},
        ),
        migrations.AddConstraint(model_name="auctionfilterpreset", constraint=models.UniqueConstraint(fields=("user", "name"), name="unique_user_auction_filter_name")),
    ]
