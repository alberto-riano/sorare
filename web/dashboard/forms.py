from __future__ import annotations

from django import forms

NOTIFY_MODE_CHOICES = [
    ("all", "All"),
    ("edge", "Edge"),
    ("drop", "Drop"),
]

RARITY_CHOICES = [
    ("limited", "Limited (amarillas)"),
    ("rare", "Rare (rojas)"),
    ("super_rare", "Super Rare (azules)"),
    ("unique", "Unique"),
]


class TelegramSettingsForm(forms.Form):
    notify_mode = forms.ChoiceField(choices=NOTIFY_MODE_CHOICES)
    notify_drop_eur = forms.DecimalField(min_value=0, decimal_places=2, max_digits=8)
    send_all_offers_below_threshold = forms.BooleanField(required=False)
    send_run_start_message = forms.BooleanField(required=False)
    send_single_message = forms.BooleanField(required=False)
    include_player_preview = forms.BooleanField(required=False)
    rarity = forms.ChoiceField(choices=RARITY_CHOICES)
    season_year = forms.IntegerField(required=False, min_value=2020, max_value=2100)
    in_season_year = forms.IntegerField(required=False, min_value=2020, max_value=2100)
    classic_players = forms.CharField(widget=forms.Textarea(attrs={"rows": 10}))
    in_season_players = forms.CharField(widget=forms.Textarea(attrs={"rows": 8}), required=False)


class BidScheduleForm(forms.Form):
    identifier = forms.CharField(max_length=500)
    euros = forms.DecimalField(min_value=0.01, decimal_places=2, max_digits=8)
    hora = forms.CharField(max_length=8, required=False, help_text="HH:MM o HH:MM:SS")
    now = forms.BooleanField(required=False)
    sniper = forms.BooleanField(required=False)
    background = forms.BooleanField(required=False)
    use_credit = forms.BooleanField(required=False, initial=True)

    def clean(self):
        cleaned_data = super().clean()
        now = bool(cleaned_data.get("now"))
        sniper = bool(cleaned_data.get("sniper"))
        hora = str(cleaned_data.get("hora") or "").strip()

        if not now and not sniper and not hora:
            raise forms.ValidationError("Debes indicar hora o activar now/sniper.")
        return cleaned_data


class ExportCardsForm(forms.Form):
    rarity = forms.ChoiceField(
        choices=[
            ("limited", "Amarillas (limited)"),
            ("rare", "Rojas (rare)"),
            ("super_rare", "Azules (super_rare)"),
        ],
        initial="super_rare",
    )
    max_cards = forms.IntegerField(min_value=1, max_value=5000, initial=10)


