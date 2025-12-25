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
const CONFIG_PATH = "../config.txt";

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
    throw new Error("No se pudo leer el fichero de configuración: " + err.message);
  }
}

// --- Parámetros de entrada ---
const [, , ASSET_ID, PRICE_CENTS, DAYS] = process.argv;
if (!ASSET_ID || !PRICE_CENTS) {
  console.error("Uso: node vender_carta.js <asset_id> <precio_centimos> [dias_en_venta]");
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

// --- Mutaciones GraphQL ---
const PREPARE_OFFER_MUTATION = gql`
  mutation PrepareOffer($input: prepareOfferInput!) {
    prepareOffer(input: $input) {
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

const CREATE_OFFER_MUTATION = gql`
  mutation CreateSingleSaleOffer($input: createSingleSaleOfferInput!) {
    createSingleSaleOffer(input: $input) {
      tokenOffer {
        id
        startDate
        endDate
      }
      errors {
        message
      }
    }
  }
`;

// --- Firma Starkex / Mangopay (imitando tu vender_ethereum.js) ---
function buildStarkAndMangopayApproval(privateKey, fingerprint, authorizationRequest) {
  const req = { ...authorizationRequest };

  switch (req.__typename) {
    case "StarkexTransferAuthorizationRequest": {
      // Igual que en tu script ETH
      req.amount = BigInt(req.amount);
      req.nonce = BigInt(req.nonce);
      req.expirationTimestamp = BigInt(req.expirationTimestamp);
      const signatureTransfer = signAuthorizationRequest(privateKey, req);
      return {
        fingerprint,
        starkexTransferApproval: {
          nonce: Number(req.nonce),
          expirationTimestamp: Number(req.expirationTimestamp),
          signature: signatureTransfer,
        },
      };
    }

    case "StarkexLimitOrderAuthorizationRequest": {
      // Igual que en tu script ETH
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
      const signatureLimitOrder = signAuthorizationRequest(privateKey, req);
      return {
        fingerprint,
        starkexLimitOrderApproval: {
          nonce: Number(req.nonce),
          expirationTimestamp: Number(req.expirationTimestamp),
          signature: signatureLimitOrder,
        },
      };
    }

    case "MangopayWalletTransferAuthorizationRequest": {
      req.nonce = BigInt(req.nonce);
      const signatureWalletTransfer = signAuthorizationRequest(privateKey, req);
      return {
        fingerprint,
        mangopayWalletTransferApproval: {
          nonce: Number(req.nonce),
          signature: signatureWalletTransfer,
        },
      };
    }

    default:
      return null;
  }
}

// --- Firma SolanaTokenTransferAuthorizationRequest con @solana/kit ---
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
    console.log("TIPO RECIBIDO:", request.__typename);

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

// --- Lógica principal unificada ---
async function sellCard(assetId, priceCents, daysOnSale) {
  if (daysOnSale) {
    const now = new Date();
    const endDate = new Date(now.getTime() + daysOnSale * 24 * 60 * 60 * 1000);
    console.log(
      `(Nota: la API no soporta duración, pero mostraríamos hasta: ${endDate.toISOString()})`
    );
  }

  const prepareOfferInput = {
    sendAssetIds: [assetId],
    receiveAssetIds: [],
    settlementCurrencies: [CURRENCY],
    receiveAmount: {
      amount: priceCents.toString(),
      currency: CURRENCY,
    },
    clientMutationId: crypto.randomBytes(8).toString("hex"),
  };

  const prepareData = await client.request(PREPARE_OFFER_MUTATION, {
    input: prepareOfferInput,
  });

  const prepareOffer = prepareData.prepareOffer;
  if (prepareOffer.errors && prepareOffer.errors.length > 0) {
    console.error("Errores preparando la oferta:");
    prepareOffer.errors.forEach((e) => console.error(e.message));
    process.exit(2);
  }

  const authorizations = prepareOffer.authorizations;
  const approvals = await buildApprovalsCombined(
    PRIVATE_KEY,
    SOLANA_PRIVATE_KEY,
    authorizations
  );

  const createOfferInput = {
    approvals,
    dealId: crypto.randomBytes(8).toString("hex"),
    assetId: assetId,
    settlementCurrencies: [CURRENCY],
    receiveAmount: {
      amount: priceCents.toString(),
      currency: CURRENCY,
    },
    clientMutationId: crypto.randomBytes(8).toString("hex"),
  };

  const createData = await client.request(CREATE_OFFER_MUTATION, {
    input: createOfferInput,
  });

  const { tokenOffer, errors: createErrors } =
    createData.createSingleSaleOffer;

  if (createErrors && createErrors.length > 0) {
    console.error("Errores creando la oferta:");
    createErrors.forEach((e) => console.error(e.message));
    process.exit(2);
  }

  console.log("¡Oferta creada con éxito!");
  console.log(tokenOffer);
}

// --- Invocación principal ---
sellCard(ASSET_ID, PRICE_CENTS, DAYS).catch(console.error);