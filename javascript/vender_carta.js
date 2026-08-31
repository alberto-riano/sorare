import { GraphQLClient, gql } from "graphql-request";
import crypto from "crypto";
import fs from "fs";
import { signAuthorizationRequest } from "@sorare/crypto";
import {
  buildEthereumBankTransferApproval,
  eurCentsToValidWei,
  weiToEthLabel,
} from "./ethereum_bank_transfer.js";
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
const DIRECT_OFFER_MODE = process.argv[2] === "--direct-offer";
const inputArgs = DIRECT_OFFER_MODE ? process.argv.slice(3) : process.argv.slice(2);
const currencyIndex = inputArgs.indexOf("--currency");
const PAYMENT_CURRENCY = currencyIndex >= 0 ? inputArgs[currencyIndex + 1] : "EUR";
const [ASSET_ID, directManagerOrPrice, directPriceOrDays, directHoursOrMinimum] = inputArgs;
const MANAGER_SLUG = DIRECT_OFFER_MODE ? directManagerOrPrice : "";
const PRICE_CENTS = DIRECT_OFFER_MODE ? directPriceOrDays : directManagerOrPrice;
const DAYS = DIRECT_OFFER_MODE ? "" : directPriceOrDays;
const TRADE_MINIMUM_CENTS = DIRECT_OFFER_MODE ? "" : directHoursOrMinimum;
if (!ASSET_ID || !PRICE_CENTS) {
  console.error("Uso: node vender_carta.js <asset_id> <precio_centimos> [dias] [minimo] o --direct-offer <asset_id> <manager_slug> <centimos> [horas]");
  process.exit(1);
}
if (DIRECT_OFFER_MODE && !MANAGER_SLUG) {
  console.error("La oferta directa requiere el slug del manager receptor.");
  process.exit(1);
}
if (!["EUR", "ETH"].includes(PAYMENT_CURRENCY)) {
  console.error("La moneda de pago debe ser EUR o ETH.");
  process.exit(1);
}
const DURATION_DAYS = parseInt(DAYS || "7", 10);
if (!Number.isInteger(DURATION_DAYS) || DURATION_DAYS < 1 || DURATION_DAYS > 30) {
  console.error("La duración debe estar entre 1 y 30 días.");
  process.exit(1);
}
const TRADE_MINIMUM_AMOUNT_CENTS = Number.isFinite(parseInt(TRADE_MINIMUM_CENTS, 10))
  ? parseInt(TRADE_MINIMUM_CENTS, 10)
  : 0;
const DIRECT_OFFER_HOURS = parseInt(DIRECT_OFFER_MODE ? (directHoursOrMinimum || "48") : "48", 10);
if (!Number.isInteger(DIRECT_OFFER_HOURS) || DIRECT_OFFER_HOURS < 24 || DIRECT_OFFER_HOURS > 168) {
  console.error("La duración de la oferta debe estar entre 24 y 168 horas.");
  process.exit(1);
}
const NO_RELIST = process.argv.includes("--no-relist");
const RELIST_RETRY_COUNT = 3;
const RELIST_RETRY_DELAY_MS = 1500;

// --- Leer configuración ---
const {
  JWT_TOKEN,
  PRIVATE_KEY,
  ETHEREUM_PRIVATE_KEY,
  JWT_AUD,
  SOLANA_PRIVATE_KEY,
} = readConfig();

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
      ... on EthereumBankTransferAuthorizationRequest {
        contractAddress
        deadline
        amount
        feeAmount
        proxyAddress
        receiverAddress
        salt
        senderAddress
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
const CONFIG_QUERY = gql`
  query DirectOfferExchangeRate {
    config {
      exchangeRate {
        ethRates { eurCents }
      }
    }
  }
`;

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

const CREATE_DIRECT_OFFER_MUTATION = gql`
  mutation CreateDirectOffer($input: createDirectOfferInput!) {
    createDirectOffer(input: $input) {
      tokenOffer { id startDate endDate }
      errors { message }
    }
  }
`;

const SET_PRIVATE_MINIMUM_MUTATION = gql`
  mutation SetPrivateMinimum($input: createOrUpdateSingleBuyOfferMinPriceInput!) {
    createOrUpdateSingleBuyOfferMinPrice(input: $input) {
      card {
        assetId
        privateMinPrices {
          referenceCurrency
          eurCents
          gbpCents
          usdCents
          wei
          lamport
        }
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
        privateMinPrices {
          referenceCurrency
          eurCents
          gbpCents
          usdCents
          wei
          lamport
        }
        liveSingleSaleOffer {
          id
          blockchainId
        }
      }
    }
  }
`;

const CANCEL_OFFER_MUTATION = gql`
  mutation CancelOffer($input: cancelOfferInput!) {
    cancelOffer(input: $input) {
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
  ethereumPrivateKey,
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

    if (request.__typename === "EthereumBankTransferAuthorizationRequest") {
      if (!ethereumPrivateKey) {
        throw new Error(
          "Falta ETHEREUM_PRIVATE_KEY en config/config.txt para pagar ETH en Base."
        );
      }
      approvals.push(await buildEthereumBankTransferApproval(ethereumPrivateKey, fingerprint, request));
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatGraphQLError(error) {
  const message = String(error?.message || error || "");
  const firstLine = message.split("\n")[0].trim();
  return firstLine || "Error GraphQL";
}

async function getExistingLiveOffer(assetId) {
  try {
    const data = await client.request(CARD_LIVE_OFFER_QUERY, { assetId });
    const offer = data?.tokens?.anyCard?.liveSingleSaleOffer;
    if (!offer?.id || !offer?.blockchainId) {
      return null;
    }
    return {
      id: offer.id,
      blockchainId: offer.blockchainId,
    };
  } catch {
    return null;
  }
}

function monetaryAmountToInput(minimum) {
  if (!minimum) return null;
  const amountByCurrency = {
    EUR: minimum.eurCents,
    GBP: minimum.gbpCents,
    USD: minimum.usdCents,
    WEI: minimum.wei,
    LAMPORT: minimum.lamport,
  };
  const amount = amountByCurrency[minimum.referenceCurrency];
  if (amount === null || amount === undefined) {
    throw new Error("No se pudo interpretar la oferta mínima actual de la carta.");
  }
  return { amount: String(amount), currency: minimum.referenceCurrency };
}

async function getCurrentPrivateMinimum(assetId) {
  const data = await client.request(CARD_LIVE_OFFER_QUERY, { assetId });
  const card = data?.tokens?.anyCard;
  if (!card) throw new Error("Sorare no devolvió la carta al consultar su oferta mínima actual.");
  return monetaryAmountToInput(card.privateMinPrices);
}

function minimumMatches(actual, expected) {
  if (expected === null) return actual === null;
  return actual !== null && actual !== undefined &&
    actual.currency === expected.currency &&
    actual.amount === String(expected.amount);
}

async function setPrivateMinimum(assetId, minPrice) {
  const data = await client.request(SET_PRIVATE_MINIMUM_MUTATION, {
    input: {
      assetId,
      isPrivate: true,
      minPrice,
      clientMutationId: crypto.randomBytes(8).toString("hex"),
    },
  });
  const payload = data?.createOrUpdateSingleBuyOfferMinPrice;
  const errors = payload?.errors || [];
  if (errors.length) {
    throw new Error(errors.map((error) => error.message).join(" · "));
  }
  let confirmedMinimum = payload?.card
    ? monetaryAmountToInput(payload.card.privateMinPrices)
    : undefined;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    if (minimumMatches(confirmedMinimum, minPrice)) return;
    await sleep(500);
    confirmedMinimum = await getCurrentPrivateMinimum(assetId);
  }
  throw new Error("Sorare respondió sin confirmar el importe mínimo de intercambio solicitado.");
}

async function tryCancelOffer(liveOffer) {
  try {
    const data = await client.request(CANCEL_OFFER_MUTATION, {
      input: {
        blockchainId: liveOffer.blockchainId,
        clientMutationId: crypto.randomBytes(8).toString("hex"),
      },
    });
    const errors = data?.cancelOffer?.errors || [];
    if (!errors.length) {
      return { cancelled: true, errors: [] };
    }
    return {
      cancelled: false,
      errors: errors.map((error) => error.message),
    };
  } catch (err) {
    return {
      cancelled: false,
      errors: [formatGraphQLError(err)],
    };
  }
}

async function waitUntilOfferIsGone(assetId, expectedOfferId) {
  for (let attempt = 0; attempt < RELIST_RETRY_COUNT; attempt += 1) {
    const liveOffer = await getExistingLiveOffer(assetId);
    if (!liveOffer) {
      return true;
    }
    if (expectedOfferId && liveOffer.id !== expectedOfferId) {
      return false;
    }
    await sleep(RELIST_RETRY_DELAY_MS);
  }
  return false;
}

// --- Lógica principal unificada ---
async function sellCard(assetId, priceCents, durationDays, relistRetriesLeft = RELIST_RETRY_COUNT) {
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
    throw new Error(prepareOffer.errors.map((error) => error.message).join(" · "));
  }

  const authorizations = prepareOffer.authorizations;
  const approvals = await buildApprovalsCombined(
    PRIVATE_KEY,
    ETHEREUM_PRIVATE_KEY,
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
    // La API de Sorare recibe la duración de la oferta en segundos.
    duration: durationDays * 24 * 60 * 60,
    clientMutationId: crypto.randomBytes(8).toString("hex"),
  };

  const createData = await client.request(CREATE_OFFER_MUTATION, { input: createOfferInput });

  const { tokenOffer, errors: createErrors } =
    createData.createSingleSaleOffer;

  if (createErrors && createErrors.length > 0) {
    if (NO_RELIST && isActiveOfferError(createErrors)) {
      throw new Error("La carta ya tiene una oferta pública activa. Actualiza el inventario antes de volver a intentarlo.");
    }
    if (relistRetriesLeft > 0 && isActiveOfferError(createErrors)) {
      const existingOffer = await getExistingLiveOffer(assetId);
      if (!existingOffer) {
        throw new Error("Existe una oferta activa, pero no se pudo obtener su blockchainId para cancelarla.");
      }

      const cancelResult = await tryCancelOffer(existingOffer);
      if (!cancelResult.cancelled) {
        throw new Error(`No se pudo cancelar la oferta activa existente para relistar: ${cancelResult.errors.join(" · ")}`);
      }

      const offerCleared = await waitUntilOfferIsGone(assetId, existingOffer.id);
      if (!offerCleared) {
        throw new Error("La oferta activa sigue apareciendo tras la cancelación; Sorare no confirmó el relistado a tiempo.");
      }

      return sellCard(assetId, priceCents, durationDays, relistRetriesLeft - 1);
    }

    throw new Error(createErrors.map((error) => error.message).join(" · "));
  }

  console.log("¡Oferta creada con éxito!");
  console.log(tokenOffer);
}

async function createDirectOffer(assetId, managerSlug, amountCents, durationHours) {
  let amount;
  let settlementCurrency;
  if (PAYMENT_CURRENCY === "ETH") {
    const rateData = await client.request(CONFIG_QUERY);
    const rateCents = rateData?.config?.exchangeRate?.ethRates?.eurCents || 0;
    const wei = eurCentsToValidWei(amountCents, rateCents);
    amount = { amount: wei.toString(), currency: "WEI" };
    settlementCurrency = "WEI";
    console.log(`Pago seleccionado: Ethereum (${weiToEthLabel(wei)} ETH)`);
  } else {
    amount = { amount: amountCents.toString(), currency: CURRENCY };
    settlementCurrency = CURRENCY;
    console.log("Pago seleccionado: EUR");
  }
  const prepareData = await client.request(PREPARE_OFFER_MUTATION, {
    input: {
      sendAssetIds: [],
      receiveAssetIds: [assetId],
      settlementCurrencies: [settlementCurrency],
      sendAmount: amount,
      receiverSlug: managerSlug,
      clientMutationId: crypto.randomBytes(8).toString("hex"),
    },
  });
  const prepared = prepareData.prepareOffer;
  if (prepared.errors?.length) {
    throw new Error(prepared.errors.map((error) => error.message).join(" · "));
  }
  const approvals = await buildApprovalsCombined(
    PRIVATE_KEY,
    ETHEREUM_PRIVATE_KEY,
    SOLANA_PRIVATE_KEY,
    prepared.authorizations
  );
  const createData = await client.request(CREATE_DIRECT_OFFER_MUTATION, {
    input: {
      approvals,
      dealId: crypto.randomBytes(8).toString("hex"),
      sendAssetIds: [],
      receiveAssetIds: [assetId],
      sendAmount: amount,
      receiverSlug: managerSlug,
      duration: durationHours * 60 * 60,
      clientMutationId: crypto.randomBytes(8).toString("hex"),
    },
  });
  const payload = createData.createDirectOffer;
  if (payload.errors?.length) {
    throw new Error(payload.errors.map((error) => error.message).join(" · "));
  }
  console.log("¡Oferta directa creada con éxito!");
  console.log(payload.tokenOffer);
}

// --- Invocación principal ---
async function setMinimumAndSell() {
  let previousMinimum;
  let minimumWasChanged = false;
  if (TRADE_MINIMUM_AMOUNT_CENTS > 0) {
    previousMinimum = await getCurrentPrivateMinimum(ASSET_ID);
    await setPrivateMinimum(ASSET_ID, {
      amount: String(TRADE_MINIMUM_AMOUNT_CENTS),
      currency: CURRENCY,
    });
    minimumWasChanged = true;
    console.log(`Oferta mínima de intercambio configurada: ${TRADE_MINIMUM_AMOUNT_CENTS} céntimos.`);
  }

  try {
    await sellCard(ASSET_ID, PRICE_CENTS, DURATION_DAYS);
  } catch (saleError) {
    if (minimumWasChanged) {
      try {
        await setPrivateMinimum(ASSET_ID, previousMinimum);
      } catch (rollbackError) {
        throw new Error(
          `${formatGraphQLError(saleError)} El mínimo anterior tampoco pudo restaurarse: ${formatGraphQLError(rollbackError)}`
        );
      }
    }
    throw saleError;
  }
}

const operation = DIRECT_OFFER_MODE
  ? createDirectOffer(ASSET_ID, MANAGER_SLUG, PRICE_CENTS, DIRECT_OFFER_HOURS)
  : setMinimumAndSell();

operation.catch((err) => {
  if (err instanceof Error) {
    console.error(err.message);
  } else {
    console.error(String(err));
  }
  process.exit(1);
});
