from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from sorare_utils import build_headers, graphql_request


CARD_FIELDS = """
  assetId slug name rarityTyped seasonYear inSeasonEligible pictureUrl
  anyPlayer { slug displayName squaredPictureUrl }
  anyTeam {
    name pictureUrl
    ... on Club { domesticLeague { slug displayName } }
  }
"""

CARD_DETAILS_QUERY = f"""
query MovementCards($assetIds: [String!]!) {{
  tokens {{ anyCards(assetIds: $assetIds) {{ {CARD_FIELDS} }} }}
}}
"""

ACCOUNT_ENTRIES_QUERY = f"""
query MovementEntries($first: Int!, $after: String) {{
  currentUser {{
    slug
    accountEntries(
      first: $first,
      after: $after,
      sortType: DESC,
      entryType: [PAYMENT, PAYMENT_FEE, CREDIT_CARD_FEE, FX_FEE, REWARD]
    ) {{
      nodes {{
        id date entryType aasmState internal provisional
        amounts {{ eurCents wei referenceCurrency }}
        tokenOperation {{
          __typename
          ... on TokenBid {{
            id createdAt fiatPayment
            amounts {{ eurCents wei referenceCurrency }}
            conversionCredits {{ totalDiscount {{ eurCents wei referenceCurrency }} }}
            auction {{ id transactionDate anyCards {{ assetId }} }}
          }}
          ... on TokenOffer {{
            id transactionDate type settlementCurrencies cardPaymentProvider
            marketFeeAmounts {{ eurCents wei referenceCurrency }}
            userBuyer {{ slug }} userSeller {{ slug }}
            senderSide {{ amounts {{ eurCents wei referenceCurrency }} anyCards {{ assetId }} }}
            receiverSide {{ amounts {{ eurCents wei referenceCurrency }} anyCards {{ assetId }} }}
          }}
          ... on TokenPrimaryOffer {{
            id transactionDate cardPaymentProvider
            price {{ eurCents wei referenceCurrency }}
            anyCards {{ assetId }}
            userBuyer {{ slug }} userSeller {{ slug }}
          }}
          ... on So5Reward {{
            id slug amount {{ eurCents wei referenceCurrency }}
            rewardCards {{ anyCard {{ assetId }} }}
          }}
          ... on TokenMonetaryReward {{
            id rewardId amounts {{ eurCents wei referenceCurrency }}
          }}
        }}
      }}
      pageInfo {{ hasNextPage endCursor }}
    }}
  }}
}}
"""

CARD_REWARDS_QUERY = f"""
query CardRewards($first: Int!, $after: String) {{
  currentUser {{
    rewards(first: $first, after: $after, sport: FOOTBALL, aasmState: CLAIMED) {{
      nodes {{
        __typename id
        ... on AnyCardReward {{
          card {{ ownerSince {CARD_FIELDS} }}
        }}
      }}
      pageInfo {{ hasNextPage endCursor }}
    }}
  }}
}}
"""


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _money(amounts: dict | None) -> dict:
    amounts = amounts or {}
    cents = _decimal(amounts.get("eurCents"))
    wei = _decimal(amounts.get("wei"))
    return {
        "eur": float(abs(cents) / 100),
        "eth": float(abs(wei) / Decimal(10**18)),
        "currency": "ETH" if amounts.get("referenceCurrency") == "WEI" else "EUR",
    }


def _card(card: dict) -> dict:
    player = card.get("anyPlayer") or {}
    team = card.get("anyTeam") or {}
    league = team.get("domesticLeague") or {}
    league_slug = str(league.get("slug") or "").casefold()
    league_name = str(league.get("displayName") or "")
    is_laliga = "laliga" in league_slug or "laliga" in league_name.casefold()
    return {
        "asset_id": card.get("assetId") or "",
        "card_slug": card.get("slug") or "",
        "player": player.get("displayName") or card.get("name") or "Carta",
        "player_slug": player.get("slug") or "",
        "player_picture_url": player.get("squaredPictureUrl") or card.get("pictureUrl") or "",
        "team": team.get("name") or "—",
        "team_picture_url": team.get("pictureUrl") or "",
        "rarity": str(card.get("rarityTyped") or "unknown").lower(),
        "season_year": card.get("seasonYear"),
        "in_season": bool(card.get("inSeasonEligible")),
        "league": league_name or "—",
        "is_laliga": is_laliga,
    }


def _raw_cards_from_operation(operation: dict) -> list[dict]:
    typename = operation.get("__typename")
    raw_cards: list[dict] = []
    if typename == "TokenBid":
        raw_cards = ((operation.get("auction") or {}).get("anyCards") or [])
    elif typename == "TokenOffer":
        raw_cards = (
            ((operation.get("senderSide") or {}).get("anyCards") or [])
            + ((operation.get("receiverSide") or {}).get("anyCards") or [])
        )
    elif typename == "TokenPrimaryOffer":
        raw_cards = operation.get("anyCards") or []
    elif typename == "So5Reward":
        raw_cards = [item.get("anyCard") for item in operation.get("rewardCards") or [] if item.get("anyCard")]
    return [card for card in raw_cards if card]


def _cards_from_operation(operation: dict, card_by_asset: dict[str, dict] | None = None) -> list[dict]:
    card_by_asset = card_by_asset or {}
    raw_cards = _raw_cards_from_operation(operation)
    return [_card(card_by_asset.get(str(card.get("assetId"))) or card) for card in raw_cards]


def _cash_side(operation: dict) -> dict:
    sides = [operation.get("senderSide") or {}, operation.get("receiverSide") or {}]
    money_only = [side for side in sides if not (side.get("anyCards") or [])]
    candidates = money_only or sides
    return max(candidates, key=lambda side: _money(side.get("amounts"))["eur"])


def _movement_from_group(
    entries: list[dict],
    current_slug: str,
    card_by_asset: dict[str, dict] | None = None,
) -> dict | None:
    operation = next((entry.get("tokenOperation") for entry in entries if entry.get("tokenOperation")), None)
    if not operation:
        return None
    typename = operation.get("__typename") or ""
    cards = _cards_from_operation(operation, card_by_asset)
    occurred_at = (
        operation.get("transactionDate")
        or (operation.get("auction") or {}).get("transactionDate")
        or operation.get("createdAt")
        or entries[0].get("date")
    )
    direction = "other"
    market = "Movimiento"
    gross = _money(entries[0].get("amounts"))
    fee = {"eur": 0.0, "eth": 0.0, "currency": gross["currency"]}
    credits_eur = 0.0

    if typename == "TokenBid":
        direction, market = "purchase", "Subasta"
        gross = _money(operation.get("amounts"))
        credits_eur = sum(_money((credit or {}).get("totalDiscount"))["eur"] for credit in operation.get("conversionCredits") or [])
    elif typename == "TokenPrimaryOffer":
        direction, market = "purchase", "Compra instantánea"
        gross = _money(operation.get("price"))
    elif typename == "TokenOffer":
        buyer = str((operation.get("userBuyer") or {}).get("slug") or "").casefold()
        seller = str((operation.get("userSeller") or {}).get("slug") or "").casefold()
        own_slug = current_slug.casefold()
        direction = "sale" if seller == own_slug else "purchase" if buyer == own_slug else "other"
        market = {
            "DIRECT_OFFER": "Oferta directa",
            "SINGLE_BUY_OFFER": "Oferta pública",
            "SINGLE_SALE_OFFER": "Compra instantánea",
        }.get(operation.get("type"), "Oferta")
        gross = _money(_cash_side(operation).get("amounts"))
        fee = _money(operation.get("marketFeeAmounts"))
    elif typename in {"So5Reward", "TokenMonetaryReward"}:
        direction, market = "reward", "Recompensa"
        gross = _money(operation.get("amount") or operation.get("amounts") or entries[0].get("amounts"))
    else:
        return None

    gross_eur = gross["eur"]
    fee_eur = fee["eur"] if direction == "sale" else 0.0
    ledger_payments = [
        _money(entry.get("amounts"))["eur"]
        for entry in entries
        if entry.get("entryType") == "PAYMENT"
    ]
    ledger_payment_eur = max(ledger_payments, default=0.0)
    if direction == "sale":
        calculated_net = max(gross_eur - fee_eur, 0.0)
        net_eur = ledger_payment_eur if 0 < ledger_payment_eur <= gross_eur else calculated_net
        fee_eur = max(gross_eur - net_eur, fee_eur)
    else:
        net_eur = max(gross_eur - credits_eur, 0.0)
    category = "laliga_inseason" if cards and all(card["is_laliga"] and card["in_season"] for card in cards) else "other"
    operation_id = operation.get("id") or entries[0].get("id")
    return {
        "id": str(operation_id),
        "occurred_at": occurred_at,
        "direction": direction,
        "market": market,
        "category": category,
        "cards": cards,
        "gross_eur": round(gross_eur, 2),
        "net_eur": round(net_eur, 2),
        "fee_eur": round(fee_eur, 2),
        "credits_eur": round(credits_eur, 2),
        "currency": gross["currency"],
        "eth": gross["eth"],
        "entry_types": sorted({entry.get("entryType") for entry in entries if entry.get("entryType")}),
    }


def _card_reward(node: dict) -> dict | None:
    if node.get("__typename") != "AnyCardReward" or not node.get("card"):
        return None
    card = _card(node["card"])
    return {
        "id": f"reward:{node.get('id')}",
        "occurred_at": node["card"].get("ownerSince"),
        "direction": "reward",
        "market": "Recompensa de carta",
        "category": "laliga_inseason" if card["is_laliga"] and card["in_season"] else "other",
        "cards": [card],
        "gross_eur": 0.0,
        "net_eur": 0.0,
        "fee_eur": 0.0,
        "credits_eur": 0.0,
        "currency": "CARD",
        "eth": 0.0,
        "entry_types": ["REWARD"],
    }


def collect_movement_history(
    *,
    progress: Callable[[int, str], None] | None = None,
    headers: dict | None = None,
) -> list[dict]:
    """Descarga compras, ventas y recompensas y las agrupa por operación."""
    headers = headers or build_headers()
    entries: list[dict] = []
    current_slug = ""
    cursor = None
    page = 0
    while True:
        page += 1
        data = graphql_request(ACCOUNT_ENTRIES_QUERY, {"first": 100, "after": cursor}, headers=headers)
        current_user = data.get("currentUser") or {}
        current_slug = current_slug or str(current_user.get("slug") or "")
        connection = current_user.get("accountEntries") or {}
        entries.extend(connection.get("nodes") or [])
        if progress:
            progress(len(entries), f"{len(entries)} asientos · página {page}")
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        operation = entry.get("tokenOperation") or {}
        key = str(operation.get("id") or f"entry:{entry.get('id')}")
        grouped[key].append(entry)

    asset_ids = sorted({
        str(card.get("assetId"))
        for entry in entries
        for card in _raw_cards_from_operation(entry.get("tokenOperation") or {})
        if card.get("assetId")
    })
    card_by_asset: dict[str, dict] = {}
    for start in range(0, len(asset_ids), 100):
        chunk = asset_ids[start:start + 100]
        data = graphql_request(CARD_DETAILS_QUERY, {"assetIds": chunk}, headers=headers)
        for card in (data.get("tokens") or {}).get("anyCards") or []:
            if card and card.get("assetId"):
                card_by_asset[str(card["assetId"])] = card
        if progress:
            progress(len(entries), f"Enriqueciendo cartas {min(start + 100, len(asset_ids))}/{len(asset_ids)}")

    movements = [
        movement for group in grouped.values()
        if (movement := _movement_from_group(group, current_slug, card_by_asset))
    ]

    cursor = None
    while True:
        data = graphql_request(CARD_REWARDS_QUERY, {"first": 100, "after": cursor}, headers=headers)
        connection = ((data.get("currentUser") or {}).get("rewards") or {})
        known_ids = {movement["id"] for movement in movements}
        for node in connection.get("nodes") or []:
            reward = _card_reward(node)
            if reward and reward["id"] not in known_ids:
                movements.append(reward)
                known_ids.add(reward["id"])
        if progress:
            progress(len(entries), f"{len(movements)} movimientos normalizados")
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    minimum = datetime.min.replace(tzinfo=timezone.utc).isoformat()
    movements.sort(key=lambda movement: movement.get("occurred_at") or minimum, reverse=True)
    return movements
