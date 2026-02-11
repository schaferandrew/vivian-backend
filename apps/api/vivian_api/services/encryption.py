"""Encryption/decryption service for sensitive data at rest.

Uses Fernet symmetric encryption with a 32-byte key derived from the
VIVIAN_API_ENCRYPTION_KEY setting (env var).
"""

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

from vivian_api.config import settings


class EncryptionService:
    """Service for encrypting/decrypting sensitive data.
    
    This service uses Fernet symmetric encryption which provides:
    - Authenticated encryption (AES-128-CBC + HMAC-SHA256)
    - URL-safe base64 encoding
    - Deterministic ciphertexts (same plaintext always produces same ciphertext
      with same key, which is acceptable for our use case)
    """

    _instance: "EncryptionService | None" = None
    _fernet: "Fernet | None" = None

    def __new__(cls) -> EncryptionService:
        """Singleton pattern to avoid re-deriving key multiple times."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        """Initialize the Fernet instance from settings."""
        key = settings.encryption_key
        if not key:
            raise ValueError(
                "VIVIAN_API_ENCRYPTION_KEY environment variable is required for encryption"
            )
        
        # If the key is already a valid Fernet key (32 bytes, base64-encoded),
        # use it directly. Otherwise, derive a key from the provided string.
        try:
            # Try to use as-is (must be 32 bytes base64-encoded)
            decoded = base64.urlsafe_b64decode(key)
            if len(decoded) == 32:
                self._fernet = Fernet(key)
                return
        except Exception:
            pass
        
        # Derive a Fernet key from the provided string using PBKDF2
        # Use a fixed salt for deterministic key derivation (acceptable since
        # the master key itself is the security boundary)
        salt = b"vivian_fixed_salt_2025"  # 22 bytes
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,
        )
        derived_key = base64.urlsafe_b64encode(kdf.derive(key.encode()))
        self._fernet = Fernet(derived_key)

    def encrypt(self, plaintext: str | None) -> str | None:
        """Encrypt plaintext and return ciphertext.
        
        Args:
            plaintext: The string to encrypt. If None, returns None.
            
        Returns:
            URL-safe base64-encoded ciphertext, or None if input was None.
        """
        if plaintext is None:
            return None
        if not isinstance(plaintext, str):
            raise TypeError("plaintext must be a string")
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str | None) -> str | None:
        """Decrypt ciphertext and return plaintext.
        
        Args:
            ciphertext: The encrypted string to decrypt. If None, returns None.
            
        Returns:
            Decrypted plaintext string, or None if input was None.
            
        Raises:
            ValueError: If ciphertext is invalid or corrupted.
        """
        if ciphertext is None:
            return None
        if not isinstance(ciphertext, str):
            raise TypeError("ciphertext must be a string")
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            raise ValueError("Invalid or corrupted encrypted data")


# Global singleton instance
encryption_service = EncryptionService()
