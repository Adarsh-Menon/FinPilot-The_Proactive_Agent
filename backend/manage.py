"""Local administrative commands. Never expose operator provisioning over HTTP."""
import argparse
import getpass
from sqlalchemy import select, text
from backend.app.config import Settings, ROOT
from backend.app.storage import User, database, initialize
from pwdlib import PasswordHash


def main():
    """Command-line entry point for local administration.

    Commands:
        init-db: create tables (and, on PostgreSQL, apply the row-level security migration).
        create-operator --username NAME: create an admin portal account; asks for the password twice.

    Used by: `python -m backend.manage ...` from the repository root. Operators are never created over HTTP.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init-db", "create-operator"])
    parser.add_argument("--username")
    args = parser.parse_args()
    settings = Settings()
    settings.check()
    from backend.app import crypto
    crypto.configure(crypto.LocalMasterKey(settings.master_key_file))
    engine, sessions = database(settings.database_url)
    if args.command == "init-db":
        initialize(engine)
        if engine.dialect.name == "postgresql":
            sql = (ROOT / "backend/migrations/001_tenant_rls.sql").read_text()
            with engine.begin() as connection:
                connection.execute(text(sql))
        print("Initialized development schema. Use a restricted app role for the server.")
    else:
        import re
        if not args.username or not re.fullmatch(r"[a-zA-Z0-9_.-]{3,40}", args.username):
            parser.error("A valid --username is required")
        password = getpass.getpass("Operator password (12+ characters): ")
        if not 12 <= len(password) <= 128 or password != getpass.getpass("Confirm password: "):
            parser.error("Passwords must match and contain 12-128 characters")
        with sessions() as db:
            if db.scalar(select(User).where(User.username == args.username.lower())):
                parser.error("Username already exists; roles are not changed by this command")
            db.add(User(username=args.username.lower(), password_hash=PasswordHash.recommended().hash(password), role="operator"))
            db.commit()
        print("Operator created. It has no customer membership.")
    engine.dispose()


if __name__ == "__main__":
    main()
