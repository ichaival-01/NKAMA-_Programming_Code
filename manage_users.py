"""Add, remove or list dispatcher accounts.

Run from the project root:
    python manage_users.py add aashi "Aashi"     (asks for a password, twice)
    python manage_users.py remove aashi
    python manage_users.py list

Accounts are saved in dispatchers.json. Each teammate can add their own;
never commit that file (it's listed in .gitignore).
"""
import getpass
import os
import sys
from pathlib import Path

from auth import Accounts

PATH = Path(os.environ.get("DISPATCH_USERS", Path(__file__).parent / "dispatchers.json"))


def main(args):
    accounts = Accounts(PATH)
    if not args or args[0] not in ("add", "remove", "list"):
        print(__doc__)
        return 1
    if args[0] == "list":
        if not accounts.users:
            print("No accounts yet. The fallback login 'dispatcher' is active.")
        for username, user in sorted(accounts.users.items()):
            print(f"{username:15} {user['name']}")
        return 0
    if len(args) < 2:
        print("Give a username, e.g. python manage_users.py add aashi \"Aashi\"")
        return 1
    username = args[1]
    if args[0] == "remove":
        accounts.remove(username)
        print(f"Removed {username}.")
        return 0
    name = args[2] if len(args) > 2 else username
    password = getpass.getpass(f"Password for {username}: ")
    if password != getpass.getpass("Same password again: "):
        print("The passwords didn't match. Nothing was saved.")
        return 1
    try:
        accounts.add(username, name, password)
    except ValueError as err:
        print(err)
        return 1
    print(f"Saved. {name} can now log in as '{username}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
