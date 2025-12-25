#!/usr/bin/env python3
"""
Script para probar diferentes queries y encontrar la estructura correcta de la API
"""

import requests
import json

SORARE_API_URL = "https://api.sorare.com/graphql"

JWT_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkODA4Yjc1Yi1hYTI5LTQ3ZWMtYjg4Yy05NzljMDI5NzllYTEiLCJzY3AiOiJ1c2VyIiwiYXVkIjoibXlhcHAiLCJpYXQiOjE3NjYzOTg1ODcsImV4cCI6IjE3Njg5OTA1ODciLCJqdGkiOiI0ZDg5ZDEyMy1mMmQwLTRkODItOWI0NS1mNDgzOWQ3NWEzYjgifQ.QuSKlMNqTc2x2Gp2nGmTBiKWyZXBx6LvlLADTC7MKZg"
JWT_AUD = "myapp"

headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {JWT_TOKEN}",
    "JWT-AUD": JWT_AUD
}


def test_query(name, query):
    """Test a GraphQL query"""
    print(f"\n{'=' * 80}")
    print(f"🧪 Testing: {name}")
    print(f"{'=' * 80}")

    response = requests.post(
        SORARE_API_URL,
        headers=headers,
        json={"query": query}
    )

    if response.status_code == 200:
        data = response.json()
        if "errors" in data:
            print(f"❌ Error: {json.dumps(data['errors'], indent=2)}")
        else:
            print(f"✅ Success!")
            print(json.dumps(data, indent=2)[:1000] + "...")
    else:
        print(f"❌ HTTP Error: {response.status_code}")
        print(response.text[:500])


# Test 1: Basic CurrentUser info
test_query("Basic CurrentUser", """
query {
  currentUser {
    slug
    nickname
  }
}
""")

# Test 2: Cards with minimal fields
test_query("Minimal Cards Query", """
query {
  currentUser {
    cards(rarities: [rare], first: 2) {
      nodes {
        assetId
        name
        slug
      }
    }
  }
}
""")

# Test 3: Cards with anyPlayer displayName only
test_query("Cards with anyPlayer", """
query {
  currentUser {
    cards(rarities: [rare], first: 2) {
      nodes {
        assetId
        name
        anyPlayer {
          displayName
        }
      }
    }
  }
}
""")

# Test 4: Cards with anyPositions
test_query("Cards with anyPositions", """
query {
  currentUser {
    cards(rarities: [rare], first: 2) {
      nodes {
        assetId
        name
        anyPlayer {
          displayName
        }
        anyPositions
      }
    }
  }
}
""")

# Test 5: Cards with anyTeam
test_query("Cards with anyTeam", """
query {
  currentUser {
    cards(rarities: [rare], first: 2) {
      nodes {
        assetId
        name
        anyPlayer {
          displayName
        }
        anyTeam {
          name
        }
        anyPositions
      }
    }
  }
}
""")

# Test 6: Check for lineup fields
test_query("Check lineup-related fields", """
query {
  currentUser {
    slug
    blockchainCardsInLineups
  }
}
""")

# Test 7: Try football-specific cards
test_query("Football cards", """
query {
  currentUser {
    footballCards(first: 2) {
      nodes {
        assetId
        name
      }
    }
  }
}
""")

print("\n" + "=" * 80)
print("✅ Tests completed!")
print("=" * 80)
