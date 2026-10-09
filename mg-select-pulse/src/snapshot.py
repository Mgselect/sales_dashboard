"""
Encrypted copy of the history files (leads, opportunities, retail register) for servers.

The exports contain customer names and phone numbers and the GitHub repo is public,
so the files are never committed as-is. Instead `snapshot/history.enc` holds them
encrypted (Fernet: AES-128 + HMAC). A server that has the key unpacks them into
`data/` on start-up; without the key the file is unreadable.

Key lookup: env PULSE_DATA_KEY, then st.secrets["snapshot"]["key"].

Refresh the snapshot after replacing the files in data/:
    python -m src.snapshot pack        # writes snapshot/history.enc (key from secrets, or a new one, saved)
    python -m src.snapshot check       # decrypts it and compares with data/
"""

from __future__ import annotations

import io
import os
import sys
import tarfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from src.data_loader import DATA_DIR, LEADS_FILE, OPPORTUNITIES_FILE, PROJECT_ROOT, find_retail_file

SNAPSHOT_FILE = PROJECT_ROOT / "snapshot" / "history.enc"
ALLOWED = {"leads.xlsx", "opportunities.xlsx", "retail.pdf", "retail.xlsx"}


class SnapshotError(Exception):
    pass


def data_key() -> str | None:
    key = os.environ.get("PULSE_DATA_KEY", "").strip()
    if key:
        return key
    try:
        import streamlit as st
        return str(st.secrets["snapshot"]["key"]).strip() or None
    except Exception:
        return None


def files_present() -> bool:
    return LEADS_FILE.exists() and OPPORTUNITIES_FILE.exists() and find_retail_file() is not None


def ensure_files() -> None:
    """Unpack the snapshot into data/ when the files aren't there (e.g. on Render)."""
    if files_present() or not SNAPSHOT_FILE.exists():
        return
    key = data_key()
    if not key:
        raise SnapshotError("This server has the encrypted data file but no key to open it. Add the "
                            "environment variable PULSE_DATA_KEY (on Render: your service → Environment), "
                            "then redeploy.")
    try:
        raw = Fernet(key.encode()).decrypt(SNAPSHOT_FILE.read_bytes())
    except (InvalidToken, ValueError) as exc:
        raise SnapshotError("The data key (PULSE_DATA_KEY) doesn't open the encrypted data file — "
                            "check it was copied in full.") from exc
    DATA_DIR.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name in ALLOWED:  # only the known files, no paths
                (DATA_DIR / member.name).write_bytes(tar.extractfile(member).read())


def pack() -> str:
    """Encrypt the current data/ files into snapshot/history.enc. Returns the key used."""
    files = [LEADS_FILE, OPPORTUNITIES_FILE, find_retail_file()]
    if not all(f and f.exists() for f in files):
        raise SnapshotError("data/ needs leads.xlsx, opportunities.xlsx and the retail register first.")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f in files:
            tar.add(f, arcname=f.name)
    key = data_key() or Fernet.generate_key().decode()
    SNAPSHOT_FILE.parent.mkdir(exist_ok=True)
    SNAPSHOT_FILE.write_bytes(Fernet(key.encode()).encrypt(buf.getvalue()))
    return key


def _save_key_to_secrets(key: str) -> Path:
    path = PROJECT_ROOT / ".streamlit" / "secrets.toml"  # gitignored
    text = path.read_text() if path.exists() else ""
    if "[snapshot]" not in text:
        path.write_text(text.rstrip() + f'\n\n[snapshot]\nkey = "{key}"\n')
        path.chmod(0o600)
    return path


def check() -> list[str]:
    """Decrypt the snapshot and compare every file with data/ (byte for byte)."""
    key = data_key()
    if not key:
        raise SnapshotError("No key found (PULSE_DATA_KEY or [snapshot] key in .streamlit/secrets.toml).")
    raw = Fernet(key.encode()).decrypt(SNAPSHOT_FILE.read_bytes())
    out = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():
            same = (DATA_DIR / m.name).exists() and tar.extractfile(m).read() == (DATA_DIR / m.name).read_bytes()
            out.append(f"{m.name}: {'identical to data/' if same else 'DIFFERENT from data/'}")
    return out


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["pack"]:
        had_key = data_key() is not None
        k = pack()
        print(f"Wrote {SNAPSHOT_FILE.relative_to(PROJECT_ROOT)} ({SNAPSHOT_FILE.stat().st_size / 1e6:.1f} MB).")
        if not had_key:
            where = _save_key_to_secrets(k)
            print(f"New key saved to {where.relative_to(PROJECT_ROOT)}. Add it on Render as PULSE_DATA_KEY:")
            print(k)
    elif cmd == ["check"]:
        print("\n".join(check()))
    else:
        print(__doc__)
