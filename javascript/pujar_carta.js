import { GraphQLClient, gql } from "graphql-request";
import crypto from "crypto";
import fs from "fs";
import { signAuthorizationRequest } from "@sorare/crypto";
import {
  createKeyPairFromBytes,
  createSignerFromKeyPair,
  createSignableMessage,
  getBase58Encoder,
  getBase58Decoder,
} from "@solana/kit";

// --- Lectura de fichero de configuración ---
const CONFIG_PATH = "../config/config.txt";

function readConfig(filename = CONFIG_PATH) {
  const config = {};
  try {
    const content = fs.readFileSync(filename).toString();
    content.split("\n").forEach((line) => {
      const [k, v] = line.trim().split("=");
      if (k && v) config[k.trim()] = v.trim();
    });
    return config;
  } catch (err) {
    throw new Error(
      "No se pudo leer el fichero de configuración: " + err.message
    );
  }
}

// --- Parámetros de entrada ---
const [, , AUCTION_ID, BID_AMOUNT_CENTS] = process.argv;
if (!AUCTION_ID || !BID_AMOUNT_CENTS) {
  console.error(
    "Uso: node pujar_carta.js <auction_id> <puja_en_centimos_EUR>"
  );
  console.error("");
  console.error("Ejemplo:");
  console.error(
    "  node pujar_carta.js EnglishAuction:81aec268-f12f-462c-b6bf-0a6f4197d2f9 800"
  );
  console.error("  (pujar 8.00€ en la subasta indicada)");
  process.exit(1);
}

// --- Leer configuración ---
const { JWT_TOKEN, PRIVATE_KEY, JWT_AUD, SOLANA_PRIVATE_KEY } = readConfig();

if (!JWT_TOKEN || !PRIVATE_KEY || !JWT_AUD) {
  console.error("Faltan JWT_TOKEN, PRIVATE_KEY o JWT_AUD en config.txt");
  process.exit(1);
}

const CURRENCY = "EUR";

const client = new GraphQLClient("https://api.sorare.com/graphql", {
  headers: {
    Authorization: `Bearer ${JWT_TOKEN}`,
    "JWT-AUD": JWT_AUD,
  },
});

// --- Fragmento de autorizaciones ---
const authorizationRequestFragment = gql`
  fragment AuthorizationRequestFragment on AuthorizationRequest {
    fingerprint
    request {
      __typename
      ... on StarkexLimitOrderAuthorizationRequest {
        vaultIdSell
        vaultIdBuy
        amountSell
        amountBuy
        tokenSell
        tokenBuy
        nonce
        expirationTimestamp
        feeInfo {
          feeLimit
          tokenId
          sourceVaultId
        }
      }
      ... on StarkexTransferAuthorizationRequest {
        amount
        condition
        expirationTimestamp
        nonce
        receiverPublicKey
        receiverVaultId
        senderVaultId
        token
      }
      ... on MangopayWalletTransferAuthorizationRequest {
        nonce
        amount
        currency
        operationHash
        mangopayWalletId
      }
      ... on SolanaTokenTransferAuthorizationRequest {
        leafIndex
        merkleTreeAddress
        originator
        receiverAddress
        expirationTimestamp
        nonce
        transferProxyProgramAddress
      }
    }
  }
`;

// --- Queries y Mutaciones ---
const CONFIG_QUERY = gql`
  query ConfigQuery {
    config {
      exchangeRate {
        id
      }
    }
  }
`;

const AUCTION_QUERY = gql`
  query GetAuction($auctionId: String!) {
    tokens {
      auction(id: $auctionId) {
        id
        currentPrice
        currency
        minNextBid
        endDate
        open
        bestBid {
          amounts {
            eurCents
          }
          bidder {
            ... on User {
              nickname
            }
          }
        }
        anyCards {
          name
          assetId
          anyPlayer {
            displayName
          }
          anyTeam {
            name
          }
        }
      }
    }
  }
`;

const PREPARE_BID_MUTATION = gql`
  mutation PrepareBid($input: prepareBidInput!) {
    prepareBid(input: $input) {
      authorizations {
        ...AuthorizationRequestFragment
      }
      errors {
        message
      }
    }
  }
  ${authorizationRequestFragment}
`;

const BID_MUTATION = gql`
  mutation Bid($input: bidInput!) {
    bid(input: $input) {
      tokenBid {
        id
      }
      errors {
        message
      }
    }
  }
`;

// --- Firma Starkex / Mangopay ---
function buildStarkAndMangopayApproval(
  privateKey,
  fingerprint,
  authorizationRequest
) {
  const req = { ...authorizationRequest };

  switch (req.__typename) {
    case "StarkexTransferAuthorizationRequest": {
      req.amount = BigInt(req.amount);
      req.nonce = BigInt(req.nonce);
      req.expirationTimestamp = BigInt(req.expirationTimestamp);
      const signature = signAuthorizationRequest(privateKey, req);
      return {
        fingerprint,
        starkexTransferApproval: {
          nonce: Number(req.nonce),
          expirationTimestamp: Number(req.expirationTimestamp),
          signature,
        },
      };
    }

    case "StarkexLimitOrderAuthorizationRequest": {
      req.amountSell = BigInt(req.amountSell);
      req.amountBuy = BigInt(req.amountBuy);
      req.nonce = BigInt(req.nonce);
      req.expirationTimestamp = BigInt(req.expirationTimestamp);
      if (req.feeInfo && req.feeInfo.feeLimit !== undefined) {
        req.feeInfo = {
          ...req.feeInfo,
          feeLimit: BigInt(req.feeInfo.feeLimit),
        };
      }
      const signature = signAuthorizationRequest(privateKey, req);
      return {
        fingerprint,
        starkexLimitOrderApproval: {
          nonce: Number(req.nonce),
          expirationTimestamp: Number(req.expirationTimestamp),
          signature,
        },
      };
    }

    case "MangopayWalletTransferAuthorizationRequest": {
      req.nonce = BigInt(req.nonce);
      const signature = signAuthorizationRequest(privateKey, req);
      return {
        fingerprint,
        mangopayWalletTransferApproval: {
          nonce: Number(req.nonce),
          signature,
        },
      };
    }

    default:
      return null;
  }
}

// --- Firma Solana ---
async function buildSolanaTokenTransferApproval(
  solanaPrivateKeyBase58,
  fingerprint,
  request
) {
  if (!solanaPrivateKeyBase58) {
    throw new Error(
      "Se ha recibido una autorización Solana pero falta SOLANA_PRIVATE_KEY en config.txt"
    );
  }

  const {
    leafIndex,
    merkleTreeAddress,
    originator,
    receiverAddress,
    expirationTimestamp,
    nonce,
    transferProxyProgramAddress,
  } = request;

  const message = [
    "TRANSFER",
    transferProxyProgramAddress,
    merkleTreeAddress,
    leafIndex.toString(),
    nonce.toString(),
    expirationTimestamp.toString(),
    receiverAddress,
    "0x",
    originator,
  ].join(":");

  const textEncoder = new TextEncoder();
  const messageBytes = textEncoder.encode(message);

  const secretKeyBytes = getBase58Encoder().encode(solanaPrivateKeyBase58);
  const keyPair = await createKeyPairFromBytes(secretKeyBytes);
  const signer = await createSignerFromKeyPair(keyPair);

  const messageHash = await crypto.subtle.digest("SHA-256", messageBytes);
  const signableMessage = createSignableMessage(new Uint8Array(messageHash));
  const [ret] = await signer.signMessages([signableMessage]);

  const firstKey = Object.keys(ret)[0];
  const signature = getBase58Decoder().decode(ret[firstKey]);

  return {
    fingerprint,
    solanaTokenTransferApproval: {
      signature,
      expirationTimestamp,
      nonce,
    },
  };
}

// --- Construcción de approvals combinados ---
async function buildApprovalsCombined(
  starkPrivateKey,
  solanaPrivateKeyBase58,
  authorizations
) {
  const approvals = [];

  for (const authorization of authorizations) {
    const { fingerprint, request } = authorization;
    console.log("  Tipo autorización:", request.__typename);

    if (
      request.__typename === "StarkexTransferAuthorizationRequest" ||
      request.__typename === "StarkexLimitOrderAuthorizationRequest" ||
      request.__typename === "MangopayWalletTransferAuthorizationRequest"
    ) {
      const approval = buildStarkAndMangopayApproval(
        starkPrivateKey,
        fingerprint,
        request
      );
      if (approval) approvals.push(approval);
      continue;
    }

    if (request.__typename === "SolanaTokenTransferAuthorizationRequest") {
      const solanaApproval = await buildSolanaTokenTransferApproval(
        solanaPrivateKeyBase58,
        fingerprint,
        request
      );
      approvals.push(solanaApproval);
      continue;
    }

    throw new Error("Tipo de autorización desconocido: " + request.__typename);
  }

  return approvals;
}

// --- Lógica principal ---
async function bidOnAuction(auctionId, bidAmountCents) {
  console.log("=" .repeat(70));
  console.log("🏷️  PUJAR EN SUBASTA DE SORARE");
  console.log("=" .repeat(70));
  console.log(`  Auction ID: ${auctionId}`);
  console.log(`  Puja: ${(bidAmountCents / 100).toFixed(2)}€ (${bidAmountCents} céntimos)`);
  console.log("");

  // 1. Obtener info de la subasta
  console.log("📋 Obteniendo info de la subasta...");
  const auctionData = await client.request(AUCTION_QUERY, {
    auctionId: auctionId.replace("EnglishAuction:", ""),
  });
  const auction = auctionData.tokens.auction;

  if (!auction.open) {
    console.error("❌ La subasta ya no está abierta.");
    process.exit(2);
  }

  const cardInfo = auction.anyCards[0];
  console.log(`  Carta: ${cardInfo.name}`);
  console.log(`  Jugador: ${cardInfo.anyPlayer.displayName}`);
  console.log(`  Equipo: ${cardInfo.anyTeam?.name || "N/A"}`);
  console.log(`  Puja actual: ${auction.bestBid ? (auction.bestBid.amounts.eurCents / 100).toFixed(2) + "€" : "Sin pujas"}`);
  console.log(`  Finaliza: ${auction.endDate}`);
  console.log("");

  // 2. Obtener exchangeRateId
  console.log("💱 Obteniendo exchange rate...");
  const configData = await client.request(CONFIG_QUERY);
  const exchangeRateId = configData.config.exchangeRate.id;
  console.log(`  Exchange Rate ID: ${exchangeRateId}`);
  console.log("");

  // 3. Preparar la puja
  console.log("📝 Preparando puja...");
  const prepareBidInput = {
    auctionId: auctionId,
    amount: bidAmountCents.toString(),
    settlementInfo: {
      currency: CURRENCY,
      paymentMethod: "WALLET",
      exchangeRateId: exchangeRateId,
    },
  };

  const prepareBidData = await client.request(PREPARE_BID_MUTATION, {
    input: prepareBidInput,
  });

  const prepareBid = prepareBidData.prepareBid;
  if (prepareBid.errors && prepareBid.errors.length > 0) {
    console.error("❌ Errores preparando la puja:");
    prepareBid.errors.forEach((e) => console.error("  ", e.message));
    process.exit(2);
  }

  const authorizations = prepareBid.authorizations;
  console.log(`  Autorizaciones recibidas: ${authorizations.length}`);
  console.log("");

  // 4. Firmar autorizaciones
  console.log("🔐 Firmando autorizaciones...");
  const approvals = await buildApprovalsCombined(
    PRIVATE_KEY,
    SOLANA_PRIVATE_KEY,
    authorizations
  );
  console.log(`  Approvals firmados: ${approvals.length}`);
  console.log("");

  // 5. Enviar la puja
  console.log("🚀 Enviando puja...");
  const bidInput = {
    approvals,
    auctionId: auctionId,
    amount: bidAmountCents.toString(),
    settlementInfo: {
      currency: CURRENCY,
      paymentMethod: "WALLET",
      exchangeRateId: exchangeRateId,
    },
    clientMutationId: crypto.randomBytes(8).toString("hex"),
  };

  const bidData = await client.request(BID_MUTATION, { input: bidInput });
  const bidResult = bidData.bid;

  if (bidResult.errors && bidResult.errors.length > 0) {
    console.error("❌ Errores al pujar:");
    bidResult.errors.forEach((e) => console.error("  ", e.message));
    process.exit(2);
  }

  console.log("");
  console.log("=" .repeat(70));
  console.log(`✅ ¡Puja realizada con éxito!`);
  console.log(`   Bid ID: ${bidResult.tokenBid.id}`);
  console.log(`   Cantidad: ${(bidAmountCents / 100).toFixed(2)}€`);
  console.log(`   Carta: ${cardInfo.name}`);
  console.log("=" .repeat(70));
}

// --- Invocación principal ---
bidOnAuction(AUCTION_ID, parseInt(BID_AMOUNT_CENTS)).catch((error) => {
  console.error("❌ Error:", error.message || error);
  process.exit(1);
});
