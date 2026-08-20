from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from sorare_utils import build_headers, graphql_request


CARD_FIELDS = """
  assetId slug name rarityTyped seasonYear serialNumber inSeasonEligible pictureUrl
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

COMPLETED_TRADES_QUERY = """
query CompletedTrades($first: Int!, $after: String) {
  currentUser {
    slug
    trades(first: $first, after: $after, sortByEndDate: DESC, sport: [FOOTBALL]) {
      nodes {
        __typename
        ... on TokenAuction {
          id transactionDate dealStatus
          anyCards { assetId }
          bestBid {
            id fiatPayment
            amounts { eurCents wei referenceCurrency }
            conversionCredit { totalDiscount { eurCents wei referenceCurrency } }
          }
        }
        ... on TokenOffer {
          id transactionDate dealStatus type settlementCurrencies cardPaymentProvider
          marketFeeAmounts { eurCents wei referenceCurrency }
          sender { __typename ... on User { slug } }
          receiver { __typename ... on User { slug } }
          userBuyer { slug } userSeller { slug }
          senderSide { amounts { eurCents wei referenceCurrency } anyCards { assetId } }
          receiverSide { amounts { eurCents wei referenceCurrency } anyCards { assetId } }
        }
        ... on TokenPrimaryOffer {
          id transactionDate dealStatus cardPaymentProvider
          price { eurCents wei referenceCurrency }
          anyCards { assetId }
          userBuyer { slug } userSeller { slug }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
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
        "serial_number": card.get("serialNumber"),
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


def _operation_from_trade(trade: dict) -> dict | None:
    """Adapta una transacción completada a la forma común del normalizador."""
    typename = trade.get("__typename")
    if not trade.get("transactionDate"):
        return None
    if typename in {"TokenOffer", "TokenPrimaryOffer"}:
        return trade
    if typename == "TokenAuction":
        best_bid = trade.get("bestBid") or {}
        if not best_bid.get("id"):
            return None
        return {
            "__typename": "TokenBid",
            "id": best_bid["id"],
            "createdAt": trade.get("transactionDate"),
            "fiatPayment": best_bid.get("fiatPayment"),
            "amounts": best_bid.get("amounts") or {},
            "conversionCredit": best_bid.get("conversionCredit"),
            "auction": {
                "id": trade.get("id"),
                "transactionDate": trade.get("transactionDate"),
                "anyCards": trade.get("anyCards") or [],
            },
        }
    return None


def _cash_side(operation: dict) -> dict:
    sides = [operation.get("senderSide") or {}, operation.get("receiverSide") or {}]
    money_only = [side for side in sides if not (side.get("anyCards") or [])]
    candidates = money_only or sides
    return max(candidates, key=lambda side: _money(side.get("amounts"))["eur"])


def _unique_cards(cards: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result = []
    for card in cards:
        key = str(card.get("asset_id") or card.get("card_slug") or id(card))
        if key in seen:
            continue
        seen.add(key)
        result.append(card)
    return result


def _movement_from_group(
    entries: list[dict],
    current_slug: str,
    card_by_asset: dict[str, dict] | None = None,
) -> dict | None:
    operation = next((entry.get("tokenOperation") for entry in entries if entry.get("tokenOperation")), None)
    if not operation:
        return None
    typename = operation.get("__typename") or ""
    if typename == "TokenBid" and not (operation.get("auction") or {}).get("transactionDate"):
        return None
    if typename in {"TokenOffer", "TokenPrimaryOffer"} and not operation.get("transactionDate"):
        return None
    cards = _cards_from_operation(operation, card_by_asset)
    sent_cards: list[dict] = []
    received_cards: list[dict] = []
    occurred_at = (
        operation.get("transactionDate")
        or (operation.get("auction") or {}).get("transactionDate")
        or operation.get("createdAt")
        or entries[0].get("date")
    )
    direction = "other"
    cash_direction = "other"
    market = "Movimiento"
    gross = _money(entries[0].get("amounts"))
    fee = {"eur": 0.0, "eth": 0.0, "currency": gross["currency"]}
    credits_eur = 0.0

    if typename == "TokenBid":
        direction, market = "purchase", "Subasta"
        cash_direction = direction
        gross = _money(operation.get("amounts"))
        credits_eur = _money((operation.get("conversionCredit") or {}).get("totalDiscount"))["eur"]
        received_cards = cards
    elif typename == "TokenPrimaryOffer":
        direction, market = "purchase", "Compra instantánea"
        cash_direction = direction
        gross = _money(operation.get("price"))
        received_cards = cards
    elif typename == "TokenOffer":
        buyer = str((operation.get("userBuyer") or {}).get("slug") or "").casefold()
        seller = str((operation.get("userSeller") or {}).get("slug") or "").casefold()
        own_slug = current_slug.casefold()
        direction = "sale" if seller == own_slug else "purchase" if buyer == own_slug else "other"
        cash_direction = direction
        market = {
            "DIRECT_OFFER": "Oferta directa",
            "SINGLE_BUY_OFFER": "Oferta pública",
            "SINGLE_SALE_OFFER": "Compra instantánea",
        }.get(operation.get("type"), "Oferta")
        gross = _money(_cash_side(operation).get("amounts"))
        fee = _money(operation.get("marketFeeAmounts"))
        sender_cards = _cards_from_operation({
            "__typename": "TokenPrimaryOffer",
            "anyCards": (operation.get("senderSide") or {}).get("anyCards") or [],
        }, card_by_asset)
        receiver_cards = _cards_from_operation({
            "__typename": "TokenPrimaryOffer",
            "anyCards": (operation.get("receiverSide") or {}).get("anyCards") or [],
        }, card_by_asset)
        sender_slug = str((operation.get("sender") or {}).get("slug") or "").casefold()
        receiver_slug = str((operation.get("receiver") or {}).get("slug") or "").casefold()
        if sender_slug == own_slug:
            sent_cards, received_cards = sender_cards, receiver_cards
        elif receiver_slug == own_slug:
            sent_cards, received_cards = receiver_cards, sender_cards
        elif direction == "sale":
            if sender_cards:
                sent_cards, received_cards = sender_cards, receiver_cards
            else:
                sent_cards, received_cards = receiver_cards, sender_cards
        elif direction == "purchase":
            if sender_cards:
                received_cards, sent_cards = sender_cards, receiver_cards
            else:
                received_cards, sent_cards = receiver_cards, sender_cards
        cards = _unique_cards(sent_cards + received_cards)
        if sent_cards and received_cards:
            direction = "trade"
            market = "Intercambio + dinero" if gross["eur"] else "Intercambio"
    elif typename in {"So5Reward", "TokenMonetaryReward"}:
        direction, market = "reward", "Recompensa"
        cash_direction = direction
        gross = _money(operation.get("amount") or operation.get("amounts") or entries[0].get("amounts"))
        received_cards = cards
    else:
        return None

    gross_eur = gross["eur"]
    fee_eur = fee["eur"] if cash_direction == "sale" else 0.0
    if cash_direction == "sale":
        net_eur = max(gross_eur - fee_eur, 0.0)
        fee_eur = max(gross_eur - net_eur, fee_eur)
    else:
        net_eur = max(gross_eur - credits_eur, 0.0)
    category = "laliga_inseason" if cards and all(card["is_laliga"] and card["in_season"] for card in cards) else "other"
    operation_id = operation.get("id") or entries[0].get("id")
    return {
        "id": str(operation_id),
        "occurred_at": occurred_at,
        "direction": direction,
        "cash_direction": cash_direction,
        "market": market,
        "category": category,
        "cards": cards,
        "sent_cards": sent_cards,
        "received_cards": received_cards,
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
        "sent_cards": [],
        "received_cards": [card],
        "gross_eur": 0.0,
        "net_eur": 0.0,
        "fee_eur": 0.0,
        "credits_eur": 0.0,
        "currency": "CARD",
        "eth": 0.0,
        "entry_types": ["REWARD"],
    }


def _movement_timestamp(movement: dict) -> datetime:
    value = movement.get("occurred_at")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)


def _player_card_key(card: dict) -> tuple:
    return (
        card.get("player_slug") or str(card.get("player") or "").casefold(),
        card.get("rarity"),
        card.get("season_year"),
        bool(card.get("in_season")),
    )


def build_trade_cycles(movements: list[dict]) -> list[dict]:
    """Empareja compras y ventas, priorizando la carta exacta y la cercanía temporal."""
    acquisitions: list[dict] = []
    disposals: list[dict] = []
    cycles: list[dict] = []
    for movement in movements:
        sent_cards = movement.get("sent_cards") or (
            movement.get("cards") or [] if movement.get("direction") == "sale" else []
        )
        received_cards = movement.get("received_cards") or (
            movement.get("cards") or [] if movement.get("direction") == "purchase" else []
        )

        for received_card in received_cards:
            known_cost = (
                Decimal(str(movement.get("net_eur") or 0))
                if len(received_cards) == 1
                and not sent_cards
                and (movement.get("cash_direction") or movement.get("direction")) == "purchase"
                else None
            )
            acquisitions.append({
                "card": received_card,
                "movement": movement,
                "cost_eur": known_cost,
                "timestamp": _movement_timestamp(movement),
            })

        for sold_card in sent_cards:
            sale_cash_direction = movement.get("cash_direction") or movement.get("direction")
            disposals.append({
                "card": sold_card,
                "movement": movement,
                "net_eur": (
                    Decimal(str(movement.get("net_eur") or 0))
                    if len(sent_cards) == 1 and sale_cash_direction == "sale"
                    else None
                ),
                "sent_count": len(sent_cards),
                "timestamp": _movement_timestamp(movement),
            })

    unused_acquisitions = set(range(len(acquisitions)))

    def closest_candidate(indexes: list[int], sale_at: datetime) -> int | None:
        if not indexes:
            return None
        previous = [index for index in indexes if acquisitions[index]["timestamp"] <= sale_at]
        if previous:
            return max(previous, key=lambda index: acquisitions[index]["timestamp"])
        return min(indexes, key=lambda index: acquisitions[index]["timestamp"])

    for disposal in sorted(disposals, key=lambda item: item["timestamp"]):
        sold_card = disposal["card"]
        sold_asset = str(sold_card.get("asset_id") or "")
        exact_candidates = [
            index for index in unused_acquisitions
            if sold_asset and str(acquisitions[index]["card"].get("asset_id") or "") == sold_asset
        ]
        match_index = closest_candidate(exact_candidates, disposal["timestamp"])
        if match_index is None:
            sold_key = _player_card_key(sold_card)
            equivalent_candidates = [
                index for index in unused_acquisitions
                if _player_card_key(acquisitions[index]["card"]) == sold_key
            ]
            match_index = closest_candidate(equivalent_candidates, disposal["timestamp"])
        if match_index is None:
            continue

        unused_acquisitions.remove(match_index)
        purchase = acquisitions[match_index]
        exact_card = bool(
            sold_asset
            and str(purchase["card"].get("asset_id") or "") == sold_asset
        )
        purchase_after_sale = purchase["timestamp"] > disposal["timestamp"]
        movement = disposal["movement"]
        purchase_cost = purchase.get("cost_eur")
        sale_net = disposal.get("net_eur")
        balance = sale_net - purchase_cost if sale_net is not None and purchase_cost is not None else None
        notes = []
        if not exact_card:
            buy_serial = purchase["card"].get("serial_number")
            sell_serial = sold_card.get("serial_number")
            notes.append(f"Cartas distintas: compra #{buy_serial or '—'} · venta #{sell_serial or '—'}")
        if purchase_after_sale:
            notes.append("La compra es posterior a la venta; se agrupan por jugador, rareza y temporada")
        received_in_trade = movement.get("received_cards") or []
        if received_in_trade:
            names = ", ".join(card.get("player") or "Carta" for card in received_in_trade)
            notes.append(f"La venta incluyó recibir {names}; el balance mostrado solo contempla el efectivo")
        if disposal["sent_count"] > 1:
            notes.append("Venta conjunta: no se puede repartir el importe con precisión entre las cartas")
        if purchase_cost is None:
            notes.append("Coste de adquisición no determinable porque la carta llegó en un intercambio o lote")

        cycles.append({
            "id": f"cycle:{purchase['movement'].get('id')}:{movement.get('id')}:{sold_asset}",
            "occurred_at": max(purchase["timestamp"], disposal["timestamp"]).isoformat(),
            "purchase_at": purchase["movement"].get("occurred_at"),
            "sale_at": movement.get("occurred_at"),
            "purchase": purchase["movement"],
            "sale": movement,
            "purchase_card": purchase["card"],
            "sale_card": sold_card,
            "exact_card": exact_card,
            "purchase_after_sale": purchase_after_sale,
            "purchase_cost_eur": purchase_cost,
            "sale_net_eur": sale_net,
            "balance_eur": balance,
            "notes": notes,
        })

    cycles.sort(key=lambda cycle: str(cycle.get("occurred_at") or ""), reverse=True)
    return cycles


def collect_movement_history(
    *,
    progress: Callable[[int, str], None] | None = None,
    headers: dict | None = None,
) -> list[dict]:
    """Descarga únicamente transacciones completadas y recompensas reclamadas."""
    headers = headers or build_headers()
    trades: list[dict] = []
    current_slug = ""
    cursor = None
    page = 0
    while True:
        page += 1
        data = graphql_request(COMPLETED_TRADES_QUERY, {"first": 100, "after": cursor}, headers=headers)
        current_user = data.get("currentUser") or {}
        current_slug = current_slug or str(current_user.get("slug") or "")
        connection = current_user.get("trades") or {}
        trades.extend(trade for trade in connection.get("nodes") or [] if trade.get("transactionDate"))
        if progress:
            progress(len(trades), f"{len(trades)} transacciones completadas · página {page}")
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    operations = [operation for trade in trades if (operation := _operation_from_trade(trade))]

    asset_ids = sorted({
        str(card.get("assetId"))
        for operation in operations
        for card in _raw_cards_from_operation(operation)
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
            progress(len(trades), f"Cargando cartas {min(start + 100, len(asset_ids))}/{len(asset_ids)}")

    movements = [
        movement
        for operation in operations
        if (movement := _movement_from_group([{
            "id": operation.get("id"),
            "date": operation.get("transactionDate") or (operation.get("auction") or {}).get("transactionDate"),
            "entryType": "PAYMENT",
            "amounts": operation.get("amounts") or operation.get("price") or {},
            "tokenOperation": operation,
        }], current_slug, card_by_asset))
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
            progress(len(trades), f"{len(movements)} movimientos completados y recompensas")
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    minimum = datetime.min.replace(tzinfo=timezone.utc).isoformat()
    movements.sort(key=lambda movement: movement.get("occurred_at") or minimum, reverse=True)
    return movements
