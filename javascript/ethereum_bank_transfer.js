import { encodeAbiParameters, encodePacked, keccak256 } from "viem";
import { privateKeyToAccount } from "viem/accounts";

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
