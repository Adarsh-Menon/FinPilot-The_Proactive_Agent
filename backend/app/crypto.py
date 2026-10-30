"""Envelope encryption for owner data.

Each tenant gets a random 256-bit data key. Data keys are stored only wrapped (AES-256-GCM)
by a master key that never enters the database: locally a key file outside the project
and its synced folders, in production a key management service behind the same
`MasterKey` interface. Owner records are sealed with the tenant's data key and bound to
their table and tenant, so a sealed value copied to another tenant or table cannot be
opened. Someone reading the database or a backup sees only ciphertext.
"""
import base64
import os
from pathlib import Path
from threading import Lock
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"


class MasterKey:
    """Interface that wraps and unwraps tenant data keys; implement it for a cloud KMS in production.

    Used by: KeyRing. Its local form is LocalMasterKey (~/.finpilot/master.key).
    """
    key_id = "abstract"

    def wrap(self, data_key: bytes, context: str) -> str:
        """Encrypt a data key under the master key.

        Args:
            data_key: raw 256-bit key.
            context: binding string, e.g. "dek:<tenant id>".

        Returns:
            Wrapped key (text).
        """
        raise NotImplementedError

    def unwrap(self, wrapped: str, context: str) -> bytes:
        """Decrypt a wrapped data key.

        Args:
            wrapped: value from wrap().
            context: the same binding string.

        Returns:
            Raw data key bytes.
        """
        raise NotImplementedError


class LocalMasterKey(MasterKey):
    """Development master key in a local file outside the project, created on first use.

    Used by: create_app() and manage.py. Back up this file: without it the database cannot be read.
    """

    def __init__(self, path):
        """Load the key file, creating it with a random key when missing.

        Args:
            path: key file path (MASTER_KEY_FILE).
        """
        self.path = Path(path).expanduser()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(base64.b64encode(AESGCM.generate_key(bit_length=256)).decode())
        self._aead = AESGCM(base64.b64decode(self.path.read_text().strip()))
        self.key_id = "local:" + self.path.name

    def wrap(self, data_key, context):
        """Wrap a data key with AES-256-GCM and a fresh nonce.

        Args:
            data_key: raw key.
            context: binding string.

        Returns:
            base64(nonce + ciphertext).
        """
        nonce = os.urandom(12)
        return base64.b64encode(nonce + self._aead.encrypt(nonce, data_key, context.encode())).decode()

    def unwrap(self, wrapped, context):
        """Unwrap a data key, failing if it was bound to another context.

        Args:
            wrapped: base64 value.
            context: binding string.

        Returns:
            Raw key bytes.
        """
        raw = base64.b64decode(wrapped)
        return self._aead.decrypt(raw[:12], raw[12:], context.encode())


class KeyRing:
    """Per-tenant data keys: created on first write, unwrapped once and cached in memory.

    Used by: Sealed columns in storage.py: every profile, transaction, goal, debt, reward and run is encrypted with
    it.
    """

    def __init__(self, master: MasterKey):
        """Create an empty key cache.

        Args:
            master: the MasterKey that wraps data keys.
        """
        self.master, self._cache, self._ciphers, self._lock = master, {}, {}, Lock()

    def data_key(self, connection, tenant_id, create=False):
        """The tenant's data key, unwrapped from the database (or created when allowed).

        Args:
            connection: database connection.
            tenant_id: owner id.
            create: create a key if none exists.

        Returns:
            Raw 256-bit key.

        Used by: seal(), open() and the referral flow.
        """
        from .storage import TenantKey  # avoid an import cycle with storage
        with self._lock:
            if tenant_id in self._cache:
                return self._cache[tenant_id]
        table = TenantKey.__table__
        row = connection.execute(table.select().where(table.c.tenant_id == tenant_id)).first()
        if row is None:
            if not create:
                raise KeyError("No data key for this tenant")
            key = AESGCM.generate_key(bit_length=256)
            connection.execute(table.insert().values(tenant_id=tenant_id, wrapped_key=self.master.wrap(key, f"dek:{tenant_id}"),
                master_key_id=self.master.key_id))
        else:
            key = self.master.unwrap(row.wrapped_key, f"dek:{tenant_id}")
        with self._lock:
            self._cache[tenant_id] = key
        return key

    def _aead(self, connection, tenant_id, create=False):
        """A cached AES-GCM cipher for a tenant.

        Args:
            connection: database connection.
            tenant_id: owner id.
            create: create the key if missing.

        Returns:
            An AESGCM instance.
        """
        with self._lock:
            cached = self._ciphers.get(tenant_id)
        if cached is None:
            cached = AESGCM(self.data_key(connection, tenant_id, create))
            with self._lock:
                self._ciphers[tenant_id] = cached
        return cached

    def seal(self, connection, tenant_id, table, plaintext: bytes) -> str:
        """Encrypt owner data, bound to its table and tenant so it cannot be moved elsewhere.

        Args:
            connection: database connection.
            tenant_id: owner id.
            table: table name.
            plaintext: bytes to encrypt.

        Returns:
            "v1:" + base64(nonce + ciphertext).

        Used by: Saving any sealed record.
        """
        nonce = os.urandom(12)
        aead = self._aead(connection, tenant_id, create=True)
        sealed = aead.encrypt(nonce, plaintext, f"{table}:{tenant_id}".encode())
        return f"{VERSION}:" + base64.b64encode(nonce + sealed).decode()

    def open(self, connection, tenant_id, table, sealed: str) -> bytes:
        """Decrypt owner data sealed for this tenant and table.

        Args:
            connection: database connection.
            tenant_id: owner id.
            table: table name.
            sealed: value from seal().

        Returns:
            Plaintext bytes.

        Used by: Loading any sealed record.
        """
        version, _, body = sealed.partition(":")
        if version != VERSION:
            raise ValueError("Unknown sealed format")
        raw = base64.b64decode(body)
        return self._aead(connection, tenant_id).decrypt(raw[:12], raw[12:], f"{table}:{tenant_id}".encode())


_keyring: KeyRing | None = None


def configure(master: MasterKey):
    """Install the process-wide key ring.

    Args:
        master: the MasterKey to use.

    Returns:
        The KeyRing.

    Used by: create_app() and manage.py at startup.
    """
    global _keyring
    _keyring = KeyRing(master)
    return _keyring


def keyring() -> KeyRing:
    """The configured key ring.

    Returns:
        The KeyRing; raises if configure() was not called.

    Used by: Sealed columns in storage.py.
    """
    if _keyring is None:
        raise RuntimeError("Encryption keys are not configured")
    return _keyring
