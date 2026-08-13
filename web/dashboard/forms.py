from __future__ import annotations

import json

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


class InlineBidForm(forms.Form):
    auction_id = forms.CharField(max_length=200, widget=forms.HiddenInput)
    euros = forms.DecimalField(min_value=0.01, decimal_places=2, max_digits=8)
    use_credit = forms.BooleanField(required=False, initial=True)
    currency = forms.ChoiceField(choices=(("EUR", "EUR"), ("ETH", "ETH")), initial="EUR")
    confirm = forms.BooleanField(required=True, error_messages={"required": "Confirma la puja antes de enviarla."})

    def clean_auction_id(self):
        auction_id = self.cleaned_data["auction_id"].strip()
        if not auction_id.startswith("EnglishAuction:"):
            raise forms.ValidationError("Identificador de subasta no válido.")
        return auction_id


class BatchBidForm(forms.Form):
    bids = forms.CharField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(required=True)

    def clean_bids(self):
        try:
            raw_bids = json.loads(self.cleaned_data["bids"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise forms.ValidationError("El resumen de pujas no es válido.") from exc
        if not isinstance(raw_bids, list) or not 1 <= len(raw_bids) <= 20:
            raise forms.ValidationError("Selecciona entre 1 y 20 pujas.")

        bids = []
        for raw_bid in raw_bids:
            if not isinstance(raw_bid, dict):
                raise forms.ValidationError("Hay una puja no válida.")
            form = InlineBidForm({
                "auction_id": raw_bid.get("auction_id", ""),
                "euros": raw_bid.get("euros", ""),
                "use_credit": raw_bid.get("use_credit", False),
                "currency": raw_bid.get("currency", "EUR"),
                "confirm": True,
            })
            if not form.is_valid():
                raise forms.ValidationError("Revisa los identificadores y los importes de las pujas.")
            bids.append(form.cleaned_data)
        return bids


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
