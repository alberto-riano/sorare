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
import { fileURLToPath } from "url";
import path from "path";

// --- Lectura de fichero de configuración ---
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CONFIG_PATH = path.join(__dirname, "..", "config", "config.txt");

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
const [, , ASSET_ID, PRICE_CENTS, DAYS, MIN_RECEIVE_CENTS] = process.argv;
if (!ASSET_ID || !PRICE_CENTS) {
  console.error("Uso: node vender_carta.js <asset_id> <precio_centimos> [dias_en_venta] [min_receive_centimos]");
  process.exit(1);
}
const DURATION_DAYS = parseInt(DAYS, 10) || 7;  // por defecto 7 días
const MIN_RECEIVE_AMOUNT_CENTS = Number.isFinite(parseInt(MIN_RECEIVE_CENTS, 10))
  ? parseInt(MIN_RECEIVE_CENTS, 10)
  : 0;

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

const CARD_LIVE_OFFER_QUERY = gql`
  query CardLiveOffer($assetId: String!) {
    tokens {
      anyCard(assetId: $assetId) {
        slug
        liveSingleSaleOffer {
          id
        }
      }
    }
  }
`;

const CANCEL_SINGLE_SALE_OFFER_MUTATION = gql`
  mutation CancelSingleSaleOffer($input: cancelSingleSaleOfferInput!) {
    cancelSingleSaleOffer(input: $input) {
      errors {
        message
      }
    }
  }
`;

const CANCEL_TOKEN_OFFER_MUTATION = gql`
  mutation CancelTokenOffer($input: cancelTokenOfferInput!) {
    cancelTokenOffer(input: $input) {
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

function isActiveOfferError(errors = []) {
  return errors.some((e) =>
    String(e.message || "").toLowerCase().includes("an active public offer already exists for these tokens")
  );
}

async function getExistingLiveOfferId(assetId) {
  try {
    const data = await client.request(CARD_LIVE_OFFER_QUERY, { assetId });
    return data?.tokens?.anyCard?.liveSingleSaleOffer?.id || null;
  } catch {
    return null;
  }
}

async function tryCancelOffer(offerId) {
  const attempts = [
    {
      mutation: CANCEL_SINGLE_SALE_OFFER_MUTATION,
      payload: { input: { offerId, clientMutationId: crypto.randomBytes(8).toString("hex") } },
      pick: (data) => data?.cancelSingleSaleOffer,
    },
    {
      mutation: CANCEL_SINGLE_SALE_OFFER_MUTATION,
      payload: { input: { id: offerId, clientMutationId: crypto.randomBytes(8).toString("hex") } },
      pick: (data) => data?.cancelSingleSaleOffer,
    },
    {
      mutation: CANCEL_TOKEN_OFFER_MUTATION,
      payload: { input: { tokenOfferId: offerId, clientMutationId: crypto.randomBytes(8).toString("hex") } },
      pick: (data) => data?.cancelTokenOffer,
    },
    {
      mutation: CANCEL_TOKEN_OFFER_MUTATION,
      payload: { input: { offerId, clientMutationId: crypto.randomBytes(8).toString("hex") } },
      pick: (data) => data?.cancelTokenOffer,
    },
  ];

  for (const attempt of attempts) {
    try {
      const data = await client.request(attempt.mutation, attempt.payload);
      const result = attempt.pick(data);
      const errors = result?.errors || [];
      if (!errors.length) {
        return true;
      }
    } catch {
      // Seguimos probando variantes de mutación/campos de input.
    }
  }

  return false;
}

// --- Lógica principal unificada ---
async function createOfferWithFallback(createOfferInput) {
  try {
    return await client.request(CREATE_OFFER_MUTATION, { input: createOfferInput });
  } catch (err) {
    const message = String(err?.message || "");
    const minFieldUnsupported =
      message.includes("minReceiveAmount") ||
      message.includes("minimumReceiveAmount") ||
      message.includes("Unknown argument") ||
      (message.includes("Field") && message.includes("is not defined"));

    if (createOfferInput.minReceiveAmount && minFieldUnsupported) {
      const fallbackInput = { ...createOfferInput };
      delete fallbackInput.minReceiveAmount;
      return client.request(CREATE_OFFER_MUTATION, { input: fallbackInput });
    }
    throw err;
  }
}

async function sellCard(assetId, priceCents, durationDays, minReceiveCents, allowRelistRetry = true) {
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

  if (minReceiveCents && minReceiveCents > 0) {
    createOfferInput.minReceiveAmount = {
      amount: minReceiveCents.toString(),
      currency: CURRENCY,
    };
  }

  const createData = await createOfferWithFallback(createOfferInput);

  const { tokenOffer, errors: createErrors } =
    createData.createSingleSaleOffer;

  if (createErrors && createErrors.length > 0) {
    if (allowRelistRetry && isActiveOfferError(createErrors)) {
      const existingOfferId = await getExistingLiveOfferId(assetId);
      if (!existingOfferId) {
        console.error("Existe una oferta activa, pero no se pudo obtener su id para cancelarla.");
        createErrors.forEach((e) => console.error(e.message));
        process.exit(2);
      }

      const cancelled = await tryCancelOffer(existingOfferId);
      if (!cancelled) {
        console.error("No se pudo cancelar la oferta activa existente para relistar.");
        createErrors.forEach((e) => console.error(e.message));
        process.exit(2);
      }

      return sellCard(assetId, priceCents, durationDays, minReceiveCents, false);
    }

    console.error("Errores creando la oferta:");
    createErrors.forEach((e) => console.error(e.message));
    process.exit(2);
  }

  console.log("¡Oferta creada con éxito!");
  console.log(tokenOffer);
}

// --- Invocación principal ---
sellCard(ASSET_ID, PRICE_CENTS, DURATION_DAYS, MIN_RECEIVE_AMOUNT_CENTS).catch((err) => {
  console.error(err);
  process.exit(1);
});