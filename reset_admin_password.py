from __future__ import annotations

import getpass
import sqlite3
import sys

import server


def reset_admin_password(email: str, new_password: str) -> None:
    if not server.DATABASE.is_file():
        raise SystemExit(f"Learnloop database not found: {server.DATABASE}")
    if len(new_password) < 10 or len(new_password) > 128:
        raise SystemExit("Password must be between 10 and 128 characters.")

    salt, password_hash = server.hash_password(new_password)
    with server.connect_db() as db:
        admin = db.execute(
            "SELECT id, email FROM users WHERE role = 'admin'"
        ).fetchone()
        if admin is None:
            raise SystemExit("No admin account exists in this database.")
        if admin["email"].casefold() != email.strip().casefold():
            raise SystemExit("That email does not match the admin account.")
        db.execute(
            "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
            (salt, password_hash, admin["id"]),
        )
        db.execute("DELETE FROM sessions WHERE user_id = ?", (admin["id"],))
    print("Admin password updated. Sign in with the new password.")


def main() -> None:
    with server.connect_db() as db:
        admin = db.execute("SELECT email FROM users WHERE role = 'admin'").fetchone()
    if admin is None:
        raise SystemExit(f"No admin account exists in {server.DATABASE}.")

    print(f"Resetting the password for the admin account: {admin['email']}")
    email = input("Type that admin email to confirm: ").strip()
    password = getpass.getpass("New password (at least 10 characters): ")
    confirmation = getpass.getpass("Confirm new password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match. No changes were made.")
    reset_admin_password(email, password)


if __name__ == "__main__":
    try:
        main()
    except sqlite3.Error as error:
        print(f"Could not update the admin account: {error}", file=sys.stderr)
        raise SystemExit(1) from error
