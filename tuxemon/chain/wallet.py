# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from tuxemon.chain.base58 import b58encode
from tuxemon.constants import paths

WALLET_SCHEMA = "solamon-wallet-v1"
DEFAULT_KDF_ITERATIONS = 390_000
DEFAULT_WALLET_PATH = paths.USER_GAME_SAVE_DIR / "solamon_wallet.json"


class WalletError(Exception):
    """Base wallet error."""


class WalletExistsError(WalletError):
    """Raised when refusing to overwrite an existing wallet."""


class WalletUnlockError(WalletError):
    """Raised when a password cannot decrypt a wallet."""


@dataclass(frozen=True)
class EncryptedWallet:
    schema: str
    public_key: str
    kdf: str
    iterations: int
    salt: str
    nonce: str
    ciphertext: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "public_key": self.public_key,
            "kdf": self.kdf,
            "iterations": self.iterations,
            "salt": self.salt,
            "nonce": self.nonce,
            "ciphertext": self.ciphertext,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EncryptedWallet:
        return cls(
            schema=str(data["schema"]),
            public_key=str(data["public_key"]),
            kdf=str(data["kdf"]),
            iterations=int(data["iterations"]),
            salt=str(data["salt"]),
            nonce=str(data["nonce"]),
            ciphertext=str(data["ciphertext"]),
        )


class UnlockedWallet:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key

    @property
    def public_key(self) -> str:
        raw_public_key = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return b58encode(raw_public_key)

    def sign_message(self, message: bytes) -> bytes:
        return self._private_key.sign(message)

    def sign_transaction(self, transaction_message: bytes) -> bytes:
        """
        Sign Solana transaction message bytes.

        Solana signatures are Ed25519 signatures over the canonical transaction
        message. A higher-level RPC client can attach this signature to the
        transaction envelope before submit.
        """
        return self.sign_message(transaction_message)

    def solana_secret_key(self) -> bytes:
        """
        Return the 64-byte Solana keypair secret in memory only.

        This is the raw Ed25519 private seed followed by the public key, which
        is the format accepted by Solana web3.js `Keypair.fromSecretKey`.
        """
        raw_private_key = _private_key_to_raw(self._private_key)
        raw_public_key = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return raw_private_key + raw_public_key


def _derive_key(password: str, salt: bytes, iterations: int) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return kdf.derive(password.encode("utf-8"))


def _private_key_to_raw(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def create_wallet(
    password: str,
    wallet_path: Path = DEFAULT_WALLET_PATH,
    *,
    overwrite: bool = False,
) -> EncryptedWallet:
    return save_private_key_wallet(
        Ed25519PrivateKey.generate(),
        password,
        wallet_path,
        overwrite=overwrite,
    )


def import_solana_keypair(
    keypair_path: Path,
    password: str,
    wallet_path: Path = DEFAULT_WALLET_PATH,
    *,
    overwrite: bool = False,
) -> EncryptedWallet:
    with keypair_path.open("r", encoding="utf-8") as keypair_file:
        secret_key = json.load(keypair_file)
    if not isinstance(secret_key, list) or len(secret_key) < 32:
        raise WalletError(f"Invalid Solana keypair file: {keypair_path}")

    private_key = Ed25519PrivateKey.from_private_bytes(
        bytes(int(value) for value in secret_key[:32])
    )
    return save_private_key_wallet(
        private_key,
        password,
        wallet_path,
        overwrite=overwrite,
    )


def save_private_key_wallet(
    private_key: Ed25519PrivateKey,
    password: str,
    wallet_path: Path = DEFAULT_WALLET_PATH,
    *,
    overwrite: bool = False,
) -> EncryptedWallet:
    if wallet_path.exists() and not overwrite:
        raise WalletExistsError(f"Wallet already exists: {wallet_path}")

    unlocked = UnlockedWallet(private_key)
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    key = _derive_key(password, salt, DEFAULT_KDF_ITERATIONS)
    ciphertext = AESGCM(key).encrypt(
        nonce,
        _private_key_to_raw(private_key),
        WALLET_SCHEMA.encode("utf-8"),
    )

    wallet = EncryptedWallet(
        schema=WALLET_SCHEMA,
        public_key=unlocked.public_key,
        kdf="pbkdf2-hmac-sha256+aes-256-gcm",
        iterations=DEFAULT_KDF_ITERATIONS,
        salt=base64.b64encode(salt).decode("ascii"),
        nonce=base64.b64encode(nonce).decode("ascii"),
        ciphertext=base64.b64encode(ciphertext).decode("ascii"),
    )
    save_encrypted_wallet(wallet, wallet_path)
    return wallet


def save_encrypted_wallet(
    wallet: EncryptedWallet,
    wallet_path: Path = DEFAULT_WALLET_PATH,
) -> None:
    wallet_path.parent.mkdir(parents=True, exist_ok=True)
    with wallet_path.open("w", encoding="utf-8") as wallet_file:
        json.dump(wallet.to_dict(), wallet_file, indent=2, sort_keys=True)


def load_encrypted_wallet(
    wallet_path: Path = DEFAULT_WALLET_PATH,
) -> EncryptedWallet:
    with wallet_path.open("r", encoding="utf-8") as wallet_file:
        data = json.load(wallet_file)
    return EncryptedWallet.from_dict(data)


def unlock_wallet(
    password: str,
    wallet_path: Path = DEFAULT_WALLET_PATH,
) -> UnlockedWallet:
    encrypted = load_encrypted_wallet(wallet_path)
    if encrypted.schema != WALLET_SCHEMA:
        raise WalletUnlockError(f"Unsupported wallet schema: {encrypted.schema}")

    salt = base64.b64decode(encrypted.salt)
    nonce = base64.b64decode(encrypted.nonce)
    ciphertext = base64.b64decode(encrypted.ciphertext)
    key = _derive_key(password, salt, encrypted.iterations)

    try:
        raw_private_key = AESGCM(key).decrypt(
            nonce,
            ciphertext,
            encrypted.schema.encode("utf-8"),
        )
    except InvalidTag as exc:
        raise WalletUnlockError("Invalid wallet password") from exc

    return UnlockedWallet(Ed25519PrivateKey.from_private_bytes(raw_private_key))
