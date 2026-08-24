from decimal import Decimal

from django.db import migrations, models


def seed_confirmed_payment_evidence(apps, schema_editor):
    evidence = apps.get_model("dashboard", "MovementPaymentEvidence")
    rows = (
        (
            "EnglishAuction:569601ec-31de-49b9-9500-8e30e05632f5",
            "local_bid_confirmed",
        ),
        (
            "EnglishAuction:ae63cf9d-ca4c-4579-b813-bb5d487e4045",
            "sorare_card_restriction_confirmed",
        ),
    )
    for auction_id, source in rows:
        evidence.objects.update_or_create(
            auction_id=auction_id,
            defaults={
                "currency": "EUR",
                "used_credit": True,
                "credit_percentage": Decimal("50.00"),
                "source": source,
            },
        )


def remove_seeded_payment_evidence(apps, schema_editor):
    evidence = apps.get_model("dashboard", "MovementPaymentEvidence")
    evidence.objects.filter(auction_id__in=(
        "EnglishAuction:569601ec-31de-49b9-9500-8e30e05632f5",
        "EnglishAuction:ae63cf9d-ca4c-4579-b813-bb5d487e4045",
    )).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0021_alter_movementsnapshot_source_version"),
    ]

    operations = [
        migrations.CreateModel(
            name="MovementPaymentEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("auction_id", models.CharField(max_length=200, unique=True)),
                ("currency", models.CharField(blank=True, choices=(("EUR", "EUR"), ("ETH", "ETH")), max_length=3)),
                ("used_credit", models.BooleanField(default=False)),
                ("credit_percentage", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("source", models.CharField(default="manual", max_length=40)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AlterField(
            model_name="movementsnapshot",
            name="source_version",
            field=models.PositiveSmallIntegerField(default=13),
        ),
        migrations.RunPython(seed_confirmed_payment_evidence, remove_seeded_payment_evidence),
    ]
