# Iteration 12: encryption of owner data at the application level

Date: 2026-10-27. Author: Claude Code. Request: the user pointed out that an admin with
database access could still read customers' transactions, and approved application-level
envelope encryption as the protection.

## Previous state and change

Profiles, transactions, imports, forecast runs and goals were stored as plaintext JSON.
In SQLite anyone with the file could read them; in PostgreSQL row-level security kept
the app and operations roles apart, but the database owner, a superuser, backups and SQL
dumps saw everything. Affected requirements: FP-18 (privacy guardrails) and the brief's
rule that admins cannot see users' data.

- **Envelope encryption.** Every customer gets a random 256-bit data key at sign-up. It
  is stored only wrapped (AES-256-GCM) by a master key that never enters the database.
  Owner records are sealed with AES-256-GCM using the customer's key and bound to their
  table and customer, so a sealed value copied to another customer or table cannot be
  opened.
- **What is sealed.** `profiles`, `transactions`, `imports`, `runs` (forecast results)
  and `goals` keep only a `sealed` column; their plaintext columns are gone. Activity,
  metrics, sessions and policy records stay readable because they are content-free.
- **Master key.** Locally a random key file at `~/.finpilot/master.key` (configurable as
  `MASTER_KEY_FILE`), outside the project, the database and synced folders. Production
  should implement the `MasterKey` interface over a key management service so that use
  of the key is access-controlled and logged; that adapter is not built yet.
- **Application code.** Models keep their Python attributes (`data`, `rows`, `result`):
  values are decrypted on first read and sealed just before writing. Queries that used
  to filter inside JSON in SQL now load the owner's rows and filter after decryption.
- **Migration.** On startup, plaintext columns from older databases are encrypted row by
  row, each value is decrypted again and compared before the plaintext column is
  dropped, all in one transaction. SQLite then rebuilds the file (VACUUM), and
  `secure_delete` is on, because a dropped column otherwise stays readable in free pages;
  the migration test caught exactly that.
- **PostgreSQL.** `tenant_keys` has row-level security like other owner tables; the app
  role may use it and the operations role has no access.

## What this protects against, and what it does not

Protected: someone reading the database directly (a DBA, the database owner, a
superuser), SQL dumps, database backups, a copied database file, and the operations
role, which now could not read customer content even if granted the tables.

Not protected: someone who controls the running application server, because it must
unwrap keys to work; someone holding both the database and the master key file; and
copies made before this change, such as the pre-migration backup or file-sync version
history. Operational controls (vault or KMS for the master key, audited owner access,
deleting plaintext copies) remain necessary.

## Acceptance and evidence

- Tests: after real API use the raw SQLite file contains no description, category,
  goal name, amount or profile field name, every owner table has only a `sealed` v1
  column and wrapped keys are stored; a sealed value fails to open under another
  customer or another table; a different master key cannot read the data; a legacy
  plaintext database is migrated with no plaintext left in the file and the migration is
  idempotent. Backend suite on 2026-10-27: 118 passed, 3 PostgreSQL tests skipped; the
  suite is slower (about four minutes) because every owner row is now decrypted.
- Real local database: backed up to `~/.finpilot/finpilot.pre-encryption.db`, then
  migrated on restart. All rows were kept (7 profiles, 586 transactions, 8 imports, 229
  runs, 1 goal), 7 customer keys were created, and none of the checked plaintext strings
  remained in the file. In the browser the demo account's forecast, 60 transactions with
  categories and context balance read back unchanged.

## Data, security and evaluation impact

Schema change: plaintext JSON columns replaced by `sealed`, new `tenant_keys` table.
Losing the master key makes all owner data unreadable; it must be backed up separately
from the database. Key rotation and the KMS adapter are not implemented. PostgreSQL
migration and grants are written but not run in this session (PostgreSQL tests were
skipped).
