"""Password-encrypted recovery for the single official Skill signing identity."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from skill_packages import (
    SigningKeyPair,
    SkillPackageError,
    SkillPackageService,
    canonical_json_bytes,
)


RECOVERY_SCHEMA = "vrcforge.official-skill-signing-recovery.v1"
PRIVATE_KEY_FILENAME = "official-skill-signing.pem"
PUBLIC_KEY_FILENAME = "official-skill-signing.pub"
_MAX_FILE_BYTES = 64 * 1024
_FINGERPRINT_PATTERN = re.compile(r"[a-f0-9]{64}")


class SkillSigningKeyMigrationError(SkillPackageError):
    """The requested signing-key backup or recovery operation was rejected."""


class SkillSigningKeyMigrationService:
    """Migrate a key while retaining the existing Skill-package governance registry."""

    def __init__(self, service: SkillPackageService, signing_dir: str | Path) -> None:
        self._service = service
        self._signing_dir = Path(signing_dir)

    @property
    def private_key_path(self) -> Path:
        return self._signing_dir / PRIVATE_KEY_FILENAME

    @property
    def public_key_path(self) -> Path:
        return self._signing_dir / PUBLIC_KEY_FILENAME

    @staticmethod
    def _passphrase_bytes(passphrase: str) -> bytes:
        if not isinstance(passphrase, str) or len(passphrase) < 8:
            raise SkillSigningKeyMigrationError(
                "The recovery passphrase must contain at least eight characters."
            )
        return passphrase.encode("utf-8")

    @staticmethod
    def _read_regular_file(path: Path, description: str) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise SkillSigningKeyMigrationError(f"The {description} must be a regular file.")
        if path.stat().st_size > _MAX_FILE_BYTES:
            raise SkillSigningKeyMigrationError(f"The {description} exceeds the size limit.")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise SkillSigningKeyMigrationError(f"The {description} could not be read.") from exc

    @staticmethod
    def _raw_public_key(private_key: Ed25519PrivateKey) -> bytes:
        return private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )

    @staticmethod
    def _decode_public_key(value: Any) -> bytes:
        if not isinstance(value, str):
            raise SkillSigningKeyMigrationError("The recovery public key is invalid.")
        try:
            public_key = base64.b64decode(value.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
            raise SkillSigningKeyMigrationError("The recovery public key is invalid.") from exc
        if len(public_key) != 32:
            raise SkillSigningKeyMigrationError("The recovery public key is invalid.")
        return public_key

    def _existing_key(self) -> tuple[Ed25519PrivateKey, bytes, str] | None:
        private_exists = self.private_key_path.exists() or self.private_key_path.is_symlink()
        public_exists = self.public_key_path.exists() or self.public_key_path.is_symlink()
        if not private_exists and not public_exists:
            return None
        if private_exists != public_exists:
            raise SkillSigningKeyMigrationError("The existing official signing keypair is incomplete.")
        private_pem = self._read_regular_file(self.private_key_path, "official private signing key")
        public_bytes = self._read_regular_file(self.public_key_path, "official public signing key")
        try:
            private_key = serialization.load_pem_private_key(private_pem, password=None)
        except (ValueError, TypeError) as exc:
            raise SkillSigningKeyMigrationError("The existing official private signing key is invalid.") from exc
        if not isinstance(private_key, Ed25519PrivateKey):
            raise SkillSigningKeyMigrationError("The official signing key must use Ed25519.")
        try:
            saved_public = public_bytes.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise SkillSigningKeyMigrationError("The existing official public signing key is invalid.") from exc
        public_key = self._decode_public_key(saved_public)
        derived_public = self._raw_public_key(private_key)
        if not hmac.compare_digest(public_key, derived_public):
            raise SkillSigningKeyMigrationError("The existing official signing keypair does not match.")
        return private_key, public_key, hashlib.sha256(public_key).hexdigest()

    def _governance(self) -> dict[str, Any]:
        registry = self._service.load_registry()
        governance = registry.get("governance", {})
        return governance if isinstance(governance, dict) else {}

    def status(self) -> dict[str, Any]:
        existing = self._existing_key()
        fingerprint = existing[2] if existing is not None else ""
        metadata = self._governance().get("official_signers", {}).get(fingerprint, {}) if fingerprint else {}
        publisher = str(metadata.get("publisher") or "") if isinstance(metadata, dict) else ""
        return {
            "ok": True,
            "key": {
                "exists": existing is not None,
                "fingerprint": fingerprint,
                "publisher": publisher,
                "publicKeyPath": str(self.public_key_path),
            },
        }

    def export_backup(self, backup_path: str | Path, passphrase: str) -> dict[str, Any]:
        password = self._passphrase_bytes(passphrase)
        if not str(backup_path or "").strip():
            raise SkillSigningKeyMigrationError("Choose an explicit recovery backup path.")
        destination = Path(backup_path).expanduser()
        if destination.exists() or destination.is_symlink():
            raise SkillSigningKeyMigrationError("The recovery backup already exists and cannot be overwritten.")
        existing = self._existing_key()
        if existing is None:
            raise SkillSigningKeyMigrationError("No official signing key is available to export.")
        private_key, public_key, fingerprint = existing
        governance = self._governance()
        official = governance.get("official_signers", {}).get(fingerprint)
        if (
            not isinstance(official, dict)
            or fingerprint not in governance.get("trusted_signers", {})
            or fingerprint in governance.get("revoked_signers", {})
        ):
            raise SkillSigningKeyMigrationError("Only an explicitly designated official signing key can be exported.")
        publisher = str(official.get("publisher") or "").strip()
        if not publisher:
            raise SkillSigningKeyMigrationError("The official signing key has no publisher identity.")
        encrypted_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(password),
        )
        document = {
            "schema": RECOVERY_SCHEMA,
            "fingerprint": fingerprint,
            "publisher": publisher,
            "publicKey": base64.b64encode(public_key).decode("ascii"),
            "encryptedPrivateKeyPem": encrypted_pem.decode("ascii"),
        }
        try:
            self._service._atomic_write_bytes(destination, canonical_json_bytes(document), mode=0o600)
        except OSError as exc:
            raise SkillSigningKeyMigrationError("The encrypted recovery backup could not be saved.") from exc
        return {"ok": True, "backupPath": str(destination), "fingerprint": fingerprint}

    def import_backup(
        self,
        backup_path: str | Path,
        passphrase: str,
        *,
        replace: bool = False,
    ) -> dict[str, Any]:
        password = self._passphrase_bytes(passphrase)
        if not str(backup_path or "").strip():
            raise SkillSigningKeyMigrationError("Choose an explicit recovery backup path.")
        source = Path(backup_path).expanduser()
        payload = self._read_regular_file(source, "encrypted recovery backup")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillSigningKeyMigrationError("The recovery backup is not valid UTF-8 JSON.") from exc
        if not isinstance(document, dict) or document.get("schema") != RECOVERY_SCHEMA:
            raise SkillSigningKeyMigrationError("The recovery backup format is unsupported.")
        fingerprint = document.get("fingerprint")
        publisher = document.get("publisher")
        encrypted_pem = document.get("encryptedPrivateKeyPem")
        if not isinstance(fingerprint, str) or _FINGERPRINT_PATTERN.fullmatch(fingerprint) is None:
            raise SkillSigningKeyMigrationError("The recovery signer fingerprint is invalid.")
        if not isinstance(publisher, str) or not publisher.strip() or len(publisher.strip()) > 120:
            raise SkillSigningKeyMigrationError("The recovery publisher identity is invalid.")
        if not isinstance(encrypted_pem, str) or not encrypted_pem.startswith(
            "-----BEGIN ENCRYPTED PRIVATE KEY-----"
        ):
            raise SkillSigningKeyMigrationError("The recovery backup does not contain an encrypted private key.")
        expected_public = self._decode_public_key(document.get("publicKey"))
        try:
            private_key = serialization.load_pem_private_key(
                encrypted_pem.encode("ascii"),
                password=password,
            )
        except (UnicodeEncodeError, ValueError, TypeError) as exc:
            raise SkillSigningKeyMigrationError(
                "The recovery passphrase is incorrect or the encrypted key is invalid."
            ) from exc
        if not isinstance(private_key, Ed25519PrivateKey):
            raise SkillSigningKeyMigrationError("The recovered signing key must use Ed25519.")
        public_key = self._raw_public_key(private_key)
        actual_fingerprint = hashlib.sha256(public_key).hexdigest()
        if not hmac.compare_digest(public_key, expected_public) or not hmac.compare_digest(
            actual_fingerprint,
            fingerprint,
        ):
            raise SkillSigningKeyMigrationError("The recovery signing key and public identity do not match.")
        existing = self._existing_key()
        if existing is not None and existing[2] != fingerprint and not replace:
            raise SkillSigningKeyMigrationError(
                "A different official signing key already exists; explicit replacement is required."
            )
        if fingerprint in self._governance().get("revoked_signers", {}):
            raise SkillSigningKeyMigrationError("A revoked signer cannot be restored as official.")
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self._service.save_signing_keypair(
            SigningKeyPair(private_pem, public_key, fingerprint),
            self.private_key_path,
            self.public_key_path,
        )
        self._service.designate_official_signer(
            fingerprint,
            reason="Restored official Skill signing key from an encrypted recovery backup.",
            publisher=publisher.strip(),
        )
        return {"ok": True, "fingerprint": fingerprint, "publisher": publisher.strip()}
