import { encodeAbiParameters, encodePacked, keccak256 } from "viem";
import { privateKeyToAccount } from "viem/accounts";

export const WEI_PAYMENT_QUANTUM = 100000000000000n;

export function eurCentsToValidWei(amountCents, eurCentsPerEth) {
  const numerator = BigInt(amountCents) * 10n ** 18n;
  const denominator = BigInt(eurCentsPerEth) * WEI_PAYMENT_QUANTUM;
  if (denominator <= 0n) throw new Error("Sorare no devolvió una tasa EUR/ETH válida.");
  const units = (numerator + denominator / 2n) / denominator;
  return (units > 0n ? units : 1n) * WEI_PAYMENT_QUANTUM;
}

export function weiToEthLabel(wei) {
  return (Number(BigInt(wei)) / 1e18).toFixed(4);
}

export async function buildEthereumBankTransferApproval(privateKey, fingerprint, request) {
  const normalizedPrivateKey = privateKey.startsWith("0x") ? privateKey : `0x${privateKey}`;
  const account = privateKeyToAccount(normalizedPrivateKey);
  const {
    senderAddress, receiverAddress, amount, feeAmount, deadline,
    salt, proxyAddress, contractAddress,
  } = request;

  if (account.address.toLowerCase() !== senderAddress.toLowerCase()) {
    throw new Error(
      "La clave privada configurada no corresponde con la dirección Base solicitada por Sorare."
    );
  }

  const encodedMessage = encodeAbiParameters(
    [
      { type: "address" }, { type: "address" }, { type: "uint256" },
      { type: "uint256" }, { type: "uint64" }, { type: "bytes32" },
      { type: "address" }, { type: "bytes" }, { type: "address" },
    ],
    [
      senderAddress, receiverAddress, amount, feeAmount, deadline,
      salt, proxyAddress, "0x", contractAddress,
    ]
  );
  const messageHash = encodePacked(["bytes"], [keccak256(encodedMessage)]);
  const signature = await account.signMessage({ message: { raw: messageHash } });
  return {
    fingerprint,
    ethereumBankTransferApproval: { signature, deadline, salt },
  };
}
