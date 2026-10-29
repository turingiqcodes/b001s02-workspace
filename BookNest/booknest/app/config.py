"""Settings, read from environment variables (or a .env file next to this project)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(env_file=ROOT / ".env"):
    # Minimal .env support so no extra package is needed. Real env vars win.
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()   # shell-style "export KEY=value"
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _default_sslmode(host):
    # A remote DB never falls back to an unencrypted connection unless told to.
    return "prefer" if host in ("localhost", "127.0.0.1", "::1") else "require"


_load_dotenv()

_host =os.environ.get("BOOKNEST_DB_HOST", "localhost")
DB = {
    "host": _host,
    "port": int(os.environ.get("BOOKNEST_DB_PORT", "5432")),
    "dbname": os.environ.get("BOOKNEST_DB_NAME", "pubs"),
    "user": os.environ.get("BOOKNEST_DB_USER", "postgres"),
    "password": os.environ.get("BOOKNEST_DB_PASSWORD", ""),
    "sslmode": os.environ.get("BOOKNEST_DB_SSLMODE", _default_sslmode(_host)),
}
# With sslmode=verify-full, the CA bundle that signed the DB's certificate (for RDS: global-bundle.pem).
if os.environ.get("BOOKNEST_DB_SSLROOTCERT"):
    DB["sslrootcert"] = os.environ["BOOKNEST_DB_SSLROOTCERT"]

# How long an API key from a login stays valid.
KEY_HOURS = int(os.environ.get("BOOKNEST_KEY_HOURS", "8"))
KEY_HOURS_REMEMBER = int(os.environ.get("BOOKNEST_KEY_HOURS_REMEMBER", str(24 * 30)))
