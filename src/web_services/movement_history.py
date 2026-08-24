from __future__ import annotations

from datetime import date, datetime, timezone
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
            conversionCredit {
              id status totalDiscount { eurCents wei referenceCurrency }
              purchase {
                __typename
                ... on TokenAuction { id }
                ... on TokenPrimaryOffer { id }
              }
            }
            conversionCredits {
              id status totalDiscount { eurCents wei referenceCurrency }
              purchase {
                __typename
                ... on TokenAuction { id }
                ... on TokenPrimaryOffer { id }
              }
            }
          }
        }
        ... on TokenOffer {
          id transactionDate dealStatus type settlementCurrencies cardPaymentProvider
          marketFeeAmounts { eurCents wei referenceCurrency }
          sender { __typename ... on User { slug } }
          receiver { __typename ... on User { slug } }
          userAcceptor { slug } userBuyer { slug } userSeller { slug }
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

PAYMENT_HISTORY_START = "2026-08-11T22:00:00Z"  # 12/08/2026 00:00 en Madrid

PAYMENT_ACCOUNTS_QUERY = """
query MovementPaymentAccounts(
  $first: Int!
  $after: String
  $currencies: [SupportedCurrency!]
  $startDate: ISO8601DateTime
) {
  currentUser {
    accountEntries(
      first: $first
      after: $after
      entryType: [PAYMENT]
      currencies: $currencies
      startDate: $startDate
      sortType: DESC
    ) {
      nodes {
        id provisional aasmState amounts { eurCents wei referenceCurrency }
        account {
          accountable {
            __typename
            ... on FiatWalletAccount { currency }
          }
        }
        tokenOperation {
          __typename
          ... on TokenBid { id }
          ... on TokenOffer { id }
          ... on TokenPrimaryOffer { id }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

CONVERSION_CREDITS_QUERY = """
query MovementConversionCredits($first: Int!, $after: String) {
  currentUser {
    sportConversionCredits(first: $first, after: $after, sport: FOOTBALL, includeUsing: true) {
      nodes {
        id status totalDiscount { eurCents wei referenceCurrency }
        purchase {
          __typename
          ... on TokenAuction { id }
          ... on TokenPrimaryOffer { id }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

REWARD_ENTRIES_QUERY = """
query RewardEntries($first: Int!, $after: String) {
  currentUser {
    accountEntries(first: $first, after: $after, entryType: [REWARD]) {
      nodes {
        id date entryType provisional aasmState
        amounts { eurCents wei referenceCurrency }
        tokenOperation {
          __typename
          ... on So5Reward {
            id slug amount { eurCents wei referenceCurrency }
            rewardCards { anyCard { assetId } }
            rewards {
              __typename
              ... on CardShardsReward {
                quantity rarity
              }
            }
            rewardConfigs {
              __typename
              ... on CardShardRewardConfig {
                quantity rarity
              }
            }
            so5Fixture { gameWeek shortDisplayName }
            so5Leaderboard { displayName(short: true, withSeasonality: false) }
            so5Ranking { ranking }
          }
          ... on TokenMonetaryReward {
            id rewardId sport amounts { eurCents wei referenceCurrency }
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

GAMEWEEK_REWARDS_QUERY = """
query GameweekRewards($first: Int!, $after: String) {
  so5 {
    allSo5Fixtures(
      first: $first
      after: $after
      eventType: CLASSIC
      sport: FOOTBALL
      future: false
    ) {
      nodes {
        ... on So5Fixture {
          id
          mySo5Rewards {
            __typename id slug aasmState amount { eurCents wei referenceCurrency }
            rewardCards { anyCard { assetId } }
            rewards {
              __typename
              ... on CardShardsReward {
                quantity rarity
              }
            }
            rewardConfigs {
              __typename
              ... on CardShardRewardConfig {
                quantity rarity
              }
            }
            so5Fixture { gameWeek shortDisplayName rewardsDeliveryDate }
            so5Leaderboard { displayName(short: true, withSeasonality: false) }
            so5Ranking { ranking }
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

PUBLIC_MANAGER_REWARDS_QUERY = """
query PublicManagerRewards($managerSlug: String!, $last: Int!, $before: String) {
  user(slug: $managerSlug) { slug nickname }
  so5 {
    allSo5Fixtures(
      last: $last
      before: $before
      eventType: CLASSIC
      sport: FOOTBALL
      future: false
    ) {
      nodes {
        __typename
        ... on So5Fixture {
          endDate rewardsDeliveryDate gameWeek shortDisplayName
          userFixtureResults(userSlug: $managerSlug) {
            eligibleOrSo5Rewards {
              __typename
              ... on So5Reward {
                id slug aasmState amount { eurCents wei referenceCurrency }
                rewardCards { anyCard { assetId } }
                rewards {
                  __typename
                  ... on CardShardsReward { quantity rarity }
                }
                rewardConfigs {
                  __typename
                  ... on CardShardRewardConfig { quantity rarity }
                }
                so5Fixture { gameWeek shortDisplayName rewardsDeliveryDate }
                so5Leaderboard { displayName(short: true, withSeasonality: false) }
                so5Ranking { ranking }
              }
            }
          }
        }
      }
      pageInfo { hasPreviousPage startCursor }
    }
  }
}
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


def _account_currency(entry: dict) -> str:
    accountable = ((entry.get("account") or {}).get("accountable") or {})
    typename = str(accountable.get("__typename") or "")
    if typename == "FiatWalletAccount":
        currency = str(accountable.get("currency") or "").upper()
        return currency if currency in {"EUR", "GBP", "USD"} else ""
    if typename in {"EthereumAccount", "StarkwareAccount", "LoomAccount"}:
        return "ETH"
    return ""


def _essence_from_operation(operation: dict) -> list[dict]:
    rewards = [
        reward for reward in operation.get("rewards") or []
        if reward.get("__typename") == "CardShardsReward"
    ]
    if rewards:
        return [{
            "quantity": int(reward.get("quantity") or 0),
            "rarity": str(reward.get("rarity") or "").lower(),
        } for reward in rewards]
    return [{
        "quantity": int(config.get("quantity") or 0),
        "rarity": str(config.get("rarity") or "").lower(),
    } for config in operation.get("rewardConfigs") or [] if config.get("__typename") == "CardShardRewardConfig"]


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
            "conversionCredits": best_bid.get("conversionCredits") or [],
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


def _purchase_credit_eur(operation: dict, gross_eur: float) -> float:
    """Obtiene el descuento aplicado sin confundirlo con el saldo histórico del crédito."""
    # El apunte confirmado de la cuenta EUR es la prueba más precisa: refleja
    # lo que realmente salió del saldo después de aplicar créditos. Es
    # preferible a ``totalDiscount``, que en créditos reutilizables puede ser
    # un acumulado de varias compras.
    paid_eur = operation.get("paidEur")
    if paid_eur is not None and operation.get("paymentCurrency") == "EUR":
        account_discount = max(gross_eur - float(paid_eur), 0.0)
        if 0 < account_discount <= gross_eur:
            return account_discount

    purchase_id = str((operation.get("auction") or {}).get("id") or operation.get("id") or "")
    credits = list(operation.get("conversionCredits") or [])
    if not credits and operation.get("conversionCredit"):
        credits = [operation["conversionCredit"]]

    # totalDiscount pertenece al crédito, no necesariamente a esta compra. La
    # referencia purchase demuestra que se consumió en esta subasta concreta.
    matched_discount = 0.0
    seen = set()
    for credit in credits:
        credit_id = str(credit.get("id") or id(credit))
        credit_purchase_id = str((credit.get("purchase") or {}).get("id") or "")
        if credit_id in seen or not purchase_id or credit_purchase_id != purchase_id:
            continue
        seen.add(credit_id)
        matched_discount += _money(credit.get("totalDiscount"))["eur"]
    if 0 < matched_discount <= gross_eur:
        return matched_discount

    return 0.0


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
    essence = []

    if typename == "TokenBid":
        direction, market = "purchase", "Subasta"
        cash_direction = direction
        gross = _money(operation.get("amounts"))
        gross["currency"] = str(operation.get("paymentCurrency") or "")
        if not gross["currency"] and operation.get("fiatPayment") is True:
            gross["currency"] = "EUR"
        credits_eur = _purchase_credit_eur(operation, gross["eur"])
        received_cards = cards
    elif typename == "TokenPrimaryOffer":
        direction, market = "purchase", "Compra instantánea"
        cash_direction = direction
        gross = _money(operation.get("price"))
        gross["currency"] = str(operation.get("paymentCurrency") or "")
        credits_eur = _purchase_credit_eur(operation, gross["eur"])
        received_cards = cards
    elif typename == "TokenOffer":
        buyer = str((operation.get("userBuyer") or {}).get("slug") or "").casefold()
        seller = str((operation.get("userSeller") or {}).get("slug") or "").casefold()
        acceptor = str((operation.get("userAcceptor") or {}).get("slug") or "").casefold()
        own_slug = current_slug.casefold()
        direction = "sale" if seller == own_slug else "purchase" if buyer == own_slug else "other"
        cash_direction = direction
        if operation.get("type") == "DIRECT_OFFER":
            market = "Oferta directa"
        elif acceptor and acceptor == seller:
            market = "Oferta pública"
        elif acceptor and acceptor == buyer:
            market = "Compra instantánea"
        else:
            market = {
                "SINGLE_BUY_OFFER": "Oferta pública",
                "SINGLE_SALE_OFFER": "Compra instantánea",
            }.get(operation.get("type"), "Oferta")
        gross = _money(_cash_side(operation).get("amounts"))
        gross["currency"] = str(operation.get("paymentCurrency") or "")
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
        essence = _essence_from_operation(operation)
        if not gross["eur"] and not gross["eth"]:
            gross["currency"] = ""
    else:
        return None

    gross_eur = gross["eur"]
    fee_eur = fee["eur"] if cash_direction == "sale" else 0.0
    if cash_direction == "sale":
        net_eur = max(gross_eur - fee_eur, 0.0)
        fee_eur = max(gross_eur - net_eur, fee_eur)
    else:
        net_eur = max(gross_eur - credits_eur, 0.0)
    category = (
        "reward"
        if direction == "reward"
        else "laliga_inseason"
        if cards and any(card["is_laliga"] for card in cards)
        else "other"
    )
    operation_id = operation.get("id") or entries[0].get("id")
    essence_quantity = sum(item["quantity"] for item in essence)
    essence_labels = []
    for item in essence:
        label = item["rarity"].replace("_", " ").title()
        if label and label not in essence_labels:
            essence_labels.append(label)
    reward_type = ""
    reward_rarity = ""
    if direction == "reward":
        if gross["eur"] or gross["eth"]:
            reward_type = "money"
        elif essence_quantity:
            reward_type = "essence"
            reward_rarity = str((essence[0] if essence else {}).get("rarity") or "")
        elif cards:
            reward_type = "card"
            reward_rarity = str((cards[0] if cards else {}).get("rarity") or "")
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
        "essence": essence,
        "essence_quantity": essence_quantity,
        "essence_description": " / ".join(essence_labels),
        "reward_type": reward_type,
        "reward_rarity": reward_rarity,
        "entry_types": sorted({entry.get("entryType") for entry in entries if entry.get("entryType")}),
    }


def _reward_market(operation: dict) -> str:
    if operation.get("__typename") != "So5Reward":
        return "Premio monetario"
    fixture = operation.get("so5Fixture") or {}
    leaderboard = operation.get("so5Leaderboard") or {}
    parts = []
    if fixture.get("shortDisplayName"):
        parts.append(str(fixture["shortDisplayName"]))
    elif fixture.get("gameWeek") is not None:
        parts.append(f"Jornada {fixture['gameWeek']}")
    if leaderboard.get("displayName"):
        parts.append(str(leaderboard["displayName"]))
    ranking = (operation.get("so5Ranking") or {}).get("ranking")
    if ranking is not None:
        parts.append(f"Puesto {ranking}")
    return " · ".join(parts) or "Jornada"


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
            notes.append(f"El intercambio incluyó recibir {names}")
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
            "category": (
                "laliga_inseason"
                if "laliga_inseason" in {
                    purchase["movement"].get("category"),
                    movement.get("category"),
                } or any(
                    bool(card.get("is_laliga"))
                    for card in (purchase["card"], sold_card)
                )
                else "other"
            ),
            "movement_ids": list(dict.fromkeys(filter(None, (
                str(purchase["movement"].get("id") or ""),
                str(movement.get("id") or ""),
            )))),
            "trade_received_cards": list(received_in_trade),
            "derived_sales": [],
            "derived_sale_net_eur": Decimal("0"),
            "realized_proceeds_eur": sale_net,
            "pending_received_cards": list(received_in_trade),
            "has_unknown_proceeds": sale_net is None,
            "is_complete": not received_in_trade and sale_net is not None,
            "notes": notes,
        })

    child_cycle_by_origin: dict[tuple[str, str], int] = {}
    for index, cycle in enumerate(cycles):
        # A downstream sale belongs to the original operation only when the exact
        # card received in the trade is the one disposed of later. Player-level
        # fallback matching is useful for standalone summaries, but would
        # otherwise attribute a different serial's proceeds to this chain.
        if not cycle.get("exact_card"):
            continue
        purchase_movement_id = str((cycle.get("purchase") or {}).get("id") or "")
        purchase_asset_id = str((cycle.get("purchase_card") or {}).get("asset_id") or "")
        if purchase_movement_id and purchase_asset_id:
            child_cycle_by_origin[(purchase_movement_id, purchase_asset_id)] = index

    merged_cycle_indexes: set[int] = set()
    enriched_cycle_indexes: set[int] = set()

    def enrich_cycle(index: int, ancestry: set[int] | None = None) -> dict:
        cycle = cycles[index]
        if index in enriched_cycle_indexes:
            return cycle
        ancestry = set(ancestry or ())
        if index in ancestry:
            return cycle
        ancestry.add(index)

        immediate = cycle.get("sale_net_eur")
        realized = immediate if immediate is not None else Decimal("0")
        unknown = immediate is None
        pending = []
        derived_sales = []
        latest_timestamp = _movement_timestamp({"occurred_at": cycle.get("occurred_at")})
        sale_movement_id = str((cycle.get("sale") or {}).get("id") or "")

        for received_card in cycle.get("trade_received_cards") or []:
            received_asset_id = str(received_card.get("asset_id") or "")
            child_index = child_cycle_by_origin.get((sale_movement_id, received_asset_id))
            if child_index is None or child_index in ancestry:
                pending.append(received_card)
                continue

            child = enrich_cycle(child_index, ancestry)
            merged_cycle_indexes.add(child_index)
            child_net = child.get("sale_net_eur")
            child_sale_at = child.get("sale_at")
            derived_sales.append({
                "card": child.get("sale_card") or received_card,
                "occurred_at": child_sale_at,
                "market": (child.get("sale") or {}).get("market") or "Venta",
                "net_eur": child_net,
                "received_cards": child.get("trade_received_cards") or [],
            })
            derived_sales.extend(child.get("derived_sales") or [])
            child_realized = child.get("realized_proceeds_eur")
            if child_realized is not None:
                realized += Decimal(str(child_realized))
            unknown = unknown or bool(child.get("has_unknown_proceeds"))
            pending.extend(child.get("pending_received_cards") or [])
            cycle["movement_ids"] = list(dict.fromkeys(
                (cycle.get("movement_ids") or []) + (child.get("movement_ids") or [])
            ))
            child_timestamp = _movement_timestamp({"occurred_at": child.get("occurred_at")})
            latest_timestamp = max(latest_timestamp, child_timestamp)

        cycle["derived_sales"] = derived_sales
        cycle["derived_sale_net_eur"] = realized - (immediate if immediate is not None else Decimal("0"))
        cycle["realized_proceeds_eur"] = realized
        cycle["pending_received_cards"] = pending
        cycle["has_unknown_proceeds"] = unknown
        cycle["is_complete"] = not pending and not unknown
        purchase_cost = cycle.get("purchase_cost_eur")
        cycle["balance_eur"] = realized - purchase_cost if purchase_cost is not None else None
        cycle["occurred_at"] = latest_timestamp.isoformat()
        if pending:
            cycle["notes"].append(
                f"{len(pending)} carta{'s' if len(pending) != 1 else ''} recibida"
                f"{'s' if len(pending) != 1 else ''} sigue{'n' if len(pending) != 1 else ''} sin venta contabilizada"
            )
        enriched_cycle_indexes.add(index)
        return cycle

    for cycle_index in range(len(cycles)):
        enrich_cycle(cycle_index)

    result = [cycle for index, cycle in enumerate(cycles) if index not in merged_cycle_indexes]
    result.sort(key=lambda cycle: str(cycle.get("occurred_at") or ""), reverse=True)
    return result


def collect_public_reward_history(
    manager_slug: str,
    *,
    start_date: date = date(2026, 8, 13),
    progress: Callable[[int, str], None] | None = None,
    headers: dict | None = None,
) -> dict:
    """Reconstruye los premios públicos de un manager desde una fecha concreta."""
    headers = headers or build_headers()
    reward_entries: list[dict] = []
    manager_nickname = manager_slug
    cursor = None
    page = 0

    while True:
        page += 1
        data = graphql_request(PUBLIC_MANAGER_REWARDS_QUERY, {
            "managerSlug": manager_slug,
            "last": 25,
            "before": cursor,
        }, headers=headers)
        public_user = data.get("user") or {}
        manager_nickname = str(public_user.get("nickname") or manager_nickname)
        connection = ((data.get("so5") or {}).get("allSo5Fixtures") or {})
        fixture_dates: list[date] = []

        for fixture in connection.get("nodes") or []:
            if fixture.get("__typename") != "So5Fixture":
                continue
            fixture_end = _movement_timestamp({"occurred_at": fixture.get("endDate")})
            if fixture.get("endDate"):
                fixture_dates.append(fixture_end.date())
                if fixture_end.date() < start_date:
                    continue
            results = fixture.get("userFixtureResults") or {}
            for reward in results.get("eligibleOrSo5Rewards") or []:
                if reward.get("__typename") != "So5Reward":
                    continue
                reward_fixture = reward.get("so5Fixture") or {}
                reward_entries.append({
                    "id": f"public:{reward.get('id')}",
                    "date": (
                        reward_fixture.get("rewardsDeliveryDate")
                        or fixture.get("rewardsDeliveryDate")
                        or fixture.get("endDate")
                    ),
                    "entryType": "REWARD",
                    "provisional": False,
                    "aasmState": str(reward.get("aasmState") or "").upper(),
                    "amounts": reward.get("amount") or {},
                    "tokenOperation": reward,
                })

        if progress:
            progress(len(reward_entries), f"{len(reward_entries)} recompensas públicas · página {page}")
        page_info = connection.get("pageInfo") or {}
        reached_start = bool(fixture_dates and min(fixture_dates) < start_date)
        if reached_start or not page_info.get("hasPreviousPage"):
            break
        cursor = page_info.get("startCursor")

    reward_operations = [entry.get("tokenOperation") or {} for entry in reward_entries]
    asset_ids = sorted({
        str(card.get("assetId"))
        for operation in reward_operations
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
            progress(len(reward_entries), f"Cargando cartas {min(start + 100, len(asset_ids))}/{len(asset_ids)}")

    movements = []
    known_ids: set[str] = set()
    for entry in reward_entries:
        reward = _movement_from_group([entry], manager_slug, card_by_asset)
        if not reward or reward["id"] in known_ids:
            continue
        reward["market"] = _reward_market(entry.get("tokenOperation") or {})
        movements.append(reward)
        known_ids.add(reward["id"])

    minimum = datetime.min.replace(tzinfo=timezone.utc).isoformat()
    movements.sort(key=lambda movement: movement.get("occurred_at") or minimum, reverse=True)
    if progress:
        progress(len(movements), f"{len(movements)} recompensas públicas guardadas")
    return {
        "manager_slug": str(public_user.get("slug") or manager_slug),
        "manager_nickname": manager_nickname,
        "movements": movements,
    }


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

    # La lista global conserva la relación crédito -> compra también cuando el
    # crédito no aparece dentro de bestBid (y para Instant Buy).
    credits_by_purchase: dict[str, list[dict]] = {}
    cursor = None
    credit_page = 0
    while True:
        credit_page += 1
        data = graphql_request(CONVERSION_CREDITS_QUERY, {"first": 100, "after": cursor}, headers=headers)
        connection = ((data.get("currentUser") or {}).get("sportConversionCredits") or {})
        for credit in connection.get("nodes") or []:
            purchase_id = str((credit.get("purchase") or {}).get("id") or "")
            if purchase_id:
                credits_by_purchase.setdefault(purchase_id, []).append(credit)
        if progress:
            progress(len(trades), f"Verificando créditos usados · página {credit_page}")
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    for operation in operations:
        purchase_id = str((operation.get("auction") or {}).get("id") or operation.get("id") or "")
        related_credits = credits_by_purchase.get(purchase_id) or []
        if related_credits:
            existing = list(operation.get("conversionCredits") or [])
            if not existing and operation.get("conversionCredit"):
                existing = [operation["conversionCredit"]]
            operation["conversionCredits"] = list({
                str(credit.get("id") or id(credit)): credit
                for credit in existing + related_credits
            }.values())

    payment_currencies: dict[str, set[str]] = {}
    payment_amounts_eur: dict[str, Decimal] = {}
    # ``account.accountable`` puede ser CommonAccount y no identifica la
    # divisa. Consultar la conexión una vez por moneda sí lo hace: Sorare solo
    # devuelve en cada pasada los apuntes que realmente se pagaron con ese
    # saldo. Esto también corrige subastas denominadas en ETH pero abonadas con
    # EUR (fiatPayment puede ser false en esos casos).
    for api_currency, display_currency in (("EUR", "EUR"), ("WEI", "ETH")):
        cursor = None
        payment_page = 0
        while True:
            payment_page += 1
            data = graphql_request(PAYMENT_ACCOUNTS_QUERY, {
                "first": 100,
                "after": cursor,
                "currencies": [api_currency],
                "startDate": PAYMENT_HISTORY_START,
            }, headers=headers)
            connection = ((data.get("currentUser") or {}).get("accountEntries") or {})
            for entry in connection.get("nodes") or []:
                operation_id = str((entry.get("tokenOperation") or {}).get("id") or "")
                if (
                    operation_id
                    and not entry.get("provisional")
                    and entry.get("aasmState") == "CONFIRMED"
                ):
                    payment_currencies.setdefault(operation_id, set()).add(display_currency)
                    if display_currency == "EUR" and entry.get("amounts") is not None:
                        payment_amounts_eur[operation_id] = (
                            payment_amounts_eur.get(operation_id, Decimal("0"))
                            + Decimal(str(_money(entry.get("amounts"))["eur"]))
                        )
            if progress:
                progress(
                    len(trades),
                    f"Verificando pagos en {display_currency} · página {payment_page}",
                )
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")
    for operation in operations:
        currencies = payment_currencies.get(str(operation.get("id") or ""), set())
        payment_currency = next(iter(currencies)) if len(currencies) == 1 else ""
        operation["paymentCurrency"] = payment_currency
        operation_id = str(operation.get("id") or "")
        if operation_id in payment_amounts_eur:
            operation["paidEur"] = float(payment_amounts_eur[operation_id])

    reward_entries: list[dict] = []
    cursor = None
    fixture_page = 0
    while True:
        fixture_page += 1
        data = graphql_request(GAMEWEEK_REWARDS_QUERY, {"first": 100, "after": cursor}, headers=headers)
        connection = ((data.get("so5") or {}).get("allSo5Fixtures") or {})
        for fixture in connection.get("nodes") or []:
            for reward in fixture.get("mySo5Rewards") or []:
                reward_fixture = reward.get("so5Fixture") or {}
                reward_entries.append({
                    "id": f"gameweek:{reward.get('id')}",
                    "date": reward_fixture.get("rewardsDeliveryDate"),
                    "entryType": "REWARD",
                    "provisional": False,
                    "aasmState": "CONFIRMED",
                    "amounts": reward.get("amount") or {},
                    "tokenOperation": reward,
                })
        if progress:
            progress(len(trades), f"{len(reward_entries)} premios de jornada · página {fixture_page}")
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    cursor = None
    reward_page = 0
    while True:
        reward_page += 1
        data = graphql_request(REWARD_ENTRIES_QUERY, {"first": 100, "after": cursor}, headers=headers)
        connection = ((data.get("currentUser") or {}).get("accountEntries") or {})
        reward_entries.extend(
            entry for entry in connection.get("nodes") or []
            if not entry.get("provisional")
            and entry.get("aasmState") == "CONFIRMED"
            and (entry.get("tokenOperation") or {}).get("__typename") in {"So5Reward", "TokenMonetaryReward"}
        )
        if progress:
            progress(len(trades), f"{len(reward_entries)} recompensas confirmadas · página {reward_page}")
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    reward_operations = [entry.get("tokenOperation") or {} for entry in reward_entries]
    asset_ids = sorted({
        str(card.get("assetId"))
        for operation in operations + reward_operations
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

    reward_groups: dict[str, list[dict]] = {}
    for entry in reward_entries:
        operation = entry.get("tokenOperation") or {}
        key = str(operation.get("id") or entry.get("id"))
        reward_groups.setdefault(key, []).append(entry)
    known_ids = {movement["id"] for movement in movements}
    for entries in reward_groups.values():
        reward = _movement_from_group(entries, current_slug, card_by_asset)
        if not reward or reward["id"] in known_ids:
            continue
        reward["market"] = _reward_market(entries[0].get("tokenOperation") or {})
        movements.append(reward)
        known_ids.add(reward["id"])
    if progress:
        progress(len(trades), f"{len(movements)} movimientos completados y recompensas")

    minimum = datetime.min.replace(tzinfo=timezone.utc).isoformat()
    movements.sort(key=lambda movement: movement.get("occurred_at") or minimum, reverse=True)
    return movements
