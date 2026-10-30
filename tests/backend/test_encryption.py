import json
import sqlite3

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from backend.app import crypto
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.storage import Transaction, database, initialize
from conftest import ORIGIN, import_history, register, setup_profile

SECRET = "Secret clinic visit"


def seeded(client):
    register(client); setup_profile(client); import_history(client)
    client.post("/api/v1/transactions", json={"idempotency_key": "enc-test-1", "transaction": {"date": "2026-10-18",
        "description": SECRET, "amount_minor": -98765, "currency": "INR", "kind": "expense", "category": "Private care"}})
    client.post("/api/v1/goals", json={"name": "Hidden goal", "target_minor": 123456, "target_date": "2027-01-31"})
    client.post("/api/v1/forecasts", json={"horizon_days": 30})


def test_database_holds_only_ciphertext(tmp_path):
    db_file = tmp_path / "enc.db"
    app = create_app(Settings(database_url=f"sqlite:///{db_file}", environment="test"))
    with TestClient(app, headers=ORIGIN) as client:
        seeded(client)
        assert any(t["description"] == SECRET for t in client.get("/api/v1/transactions").json()["items"])
    raw = db_file.read_bytes()
    for private in [SECRET.encode(), b"Private care", b"Cafe meal", b"Hidden goal", b"98765", b"123456", b"opening_balance_minor"]:
        assert private not in raw  # not in tables, indexes or free pages
    conn = sqlite3.connect(db_file)
    for table in ["profiles", "transactions", "imports", "runs", "goals"]:
        columns = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
        assert "sealed" in columns and not {"data", "rows", "result"} & set(columns)
        assert all(v.startswith("v1:") for (v,) in conn.execute(f"SELECT sealed FROM {table}"))
    (wrapped,) = conn.execute("SELECT wrapped_key FROM tenant_keys").fetchone()
    assert len(wrapped) > 40


def test_ciphertext_cannot_move_between_tenants_or_tables(tmp_path):
    db_file = tmp_path / "swap.db"
    app = create_app(Settings(database_url=f"sqlite:///{db_file}", environment="test"))
    with TestClient(app, headers=ORIGIN) as client:
        seeded(client)
        with TestClient(app, headers=ORIGIN) as other:
            register(other, "customer_b"); setup_profile(other)
        conn = sqlite3.connect(db_file)
        a, b = [r[0] for r in conn.execute("SELECT tenant_id FROM profiles ORDER BY rowid")]
        (sealed_a,) = conn.execute("SELECT sealed FROM profiles WHERE tenant_id = ?", (a,)).fetchone()
        ring = crypto.keyring()
        with app.state.sessions() as db:
            with pytest.raises(InvalidTag):
                ring.open(db.connection(), b, "profiles", sealed_a)       # another tenant's key
            with pytest.raises(InvalidTag):
                ring.open(db.connection(), a, "goals", sealed_a)          # same tenant, other table
            assert json.loads(ring.open(db.connection(), a, "profiles", sealed_a))["currency"] == "INR"


def test_wrong_master_key_cannot_read_data(tmp_path):
    db_file = tmp_path / "keys.db"
    app = create_app(Settings(database_url=f"sqlite:///{db_file}", environment="test"))
    with TestClient(app, headers=ORIGIN) as client:
        seeded(client)
    other_key = tmp_path / "other.key"
    other = create_app(Settings(database_url=f"sqlite:///{db_file}", environment="test", master_key_file=str(other_key)))
    with other.state.sessions() as db:
        row = db.query(Transaction).first()
        with pytest.raises(InvalidTag):
            row.data


def test_existing_plaintext_database_is_migrated(tmp_path):
    db_file = tmp_path / "legacy.db"
    legacy = sqlite3.connect(db_file)
    legacy.executescript("""
        CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, username VARCHAR(40) UNIQUE, password_hash VARCHAR(255), role VARCHAR(20), version INTEGER);
        CREATE TABLE transactions (tenant_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE, id VARCHAR(36) NOT NULL,
            idempotency_key VARCHAR(100) NOT NULL, payload_hash VARCHAR(64) NOT NULL, data JSON NOT NULL,
            PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, idempotency_key));
        INSERT INTO users VALUES ('u1', 'old_user', 'x', 'customer', 3);
        INSERT INTO transactions VALUES ('u1', 't1', 'k1', 'h1', '{"description": "Old plaintext rent", "amount_minor": -2500000, "currency": "INR"}');
    """)
    legacy.commit(); legacy.close()
    crypto.configure(crypto.LocalMasterKey(Settings().master_key_file))
    engine, sessions = database(f"sqlite:///{db_file}")
    initialize(engine)
    raw = db_file.read_bytes()
    assert b"Old plaintext rent" not in raw
    with sessions() as db:
        assert db.get(Transaction, ("u1", "t1")).data == {"description": "Old plaintext rent", "amount_minor": -2500000, "currency": "INR"}
    initialize(engine)  # running again is a no-op
    engine.dispose()
