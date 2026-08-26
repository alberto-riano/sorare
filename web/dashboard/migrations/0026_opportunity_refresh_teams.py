from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0025_opportunity_market"),
    ]

    operations = [
        migrations.AddField(
            model_name="opportunityrefreshjob",
            name="target_team_slugs",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="opportunityrefreshjob",
            name="team_catalog",
            field=models.JSONField(default=list),
        ),
    ]
