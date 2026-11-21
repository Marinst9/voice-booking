"""
Print the environment variables for Railway, ready to paste into the service's
Variables -> Raw Editor. Fresh random secrets are generated every time you run it.

    .\\.venv\\Scripts\\python.exe scripts\\railway_variables.py

VAPI_SECRET is copied from your local .env if it's there, so the Vapi credential you already
made keeps working. Everything else is new: production should not share secrets with your laptop.

Nothing is saved anywhere: copy the output into Railway (and your password manager), then close
the window.
"""

import secrets
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def local_value(name: str) -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == name and value.strip():
            return value.strip()
    return None


def main() -> None:
    vapi_secret = local_value("VAPI_SECRET") or secrets.token_urlsafe(32)
    variables = {
        "ENVIRONMENT": "production",
        "CALENDAR_BACKEND": "postgres",
        # A Railway "reference": Railway fills in the address of the database service named Postgres.
        "DATABASE_URL": "${{Postgres.DATABASE_URL}}",
        "PORT": "8080",
        "CLIENT_IP_HEADER": "X-Real-IP",
        "API_KEY": secrets.token_urlsafe(32),
        "VAPI_SECRET": vapi_secret,
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD": secrets.token_urlsafe(18),
        "SESSION_SECRET": secrets.token_urlsafe(48),
    }
    print("# Paste everything below into Railway: your service -> Variables -> Raw Editor")
    for name, value in variables.items():
        print(f"{name}={value}")
    print()
    print("# Save ADMIN_PASSWORD and API_KEY in a password manager: you need them to log in / call the API.")
    if local_value("VAPI_SECRET"):
        print("# VAPI_SECRET is the one from your local .env, so your Vapi credential needs no change.")
    else:
        print("# VAPI_SECRET is NEW: put the same value in your Vapi Bearer Token credential.")


if __name__ == "__main__":
    main()
