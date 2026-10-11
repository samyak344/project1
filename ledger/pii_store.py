from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_DIR = PROJECT_ROOT / "ledger"

DEFAULT_STORE_PATH = DEFAULT_LEDGER_DIR / "pii_store.json"
DEFAULT_MASTER_KEY_PATH = DEFAULT_LEDGER_DIR / "keys" / "pii_master.key"

STORE_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(obj: dict[str, Any]) -> bytes:
    """
    Convert data to stable JSON bytes before encryption.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class PIIStore:
    """
    Separate encrypted, erasable PII store.

    Security model:
    - A master Fernet key encrypts per-case keys.
    - Each case has its own random Fernet key.
    - The case PII is encrypted with the case key.
    - Erasing a case deletes the case key and ciphertext.
    - Without the case key, the encrypted PII cannot be recovered.
    """

    def __init__(
        self,
        store_path: Path | str | None = None,
        master_key_path: Path | str | None = None,
    ) -> None:
        if store_path is None:
            store_path = DEFAULT_STORE_PATH

        if master_key_path is None:
            master_key_path = DEFAULT_MASTER_KEY_PATH

        self.store_path = Path(store_path)
        self.master_key_path = Path(master_key_path)

        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.master_key_path.parent.mkdir(parents=True, exist_ok=True)

        self._master_fernet = self._load_or_create_master_key()

    # -------------------------
    # Storage helpers
    # -------------------------

    def _read_store(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {
                "schema_version": STORE_SCHEMA_VERSION,
                "cases": {},
            }

        text = self.store_path.read_text(encoding="utf-8").strip()

        if not text:
            return {
                "schema_version": STORE_SCHEMA_VERSION,
                "cases": {},
            }

        return json.loads(text)

    def _write_store(self, store: dict[str, Any]) -> None:
        temp_path = self.store_path.with_name(self.store_path.name + ".tmp")

        temp_path.write_text(
            json.dumps(store, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        temp_path.replace(self.store_path)

    # -------------------------
    # Key management
    # -------------------------

    def _load_or_create_master_key(self) -> Fernet:
        if self.master_key_path.exists():
            return Fernet(self.master_key_path.read_bytes())

        store = self._read_store()

        if store.get("cases"):
            raise FileNotFoundError(
                "PII store exists but the master key is missing. "
                "Cannot safely create a new master key because existing "
                "encrypted records would become unreadable. "
                "Restore the master key or delete the store file to reset."
            )

        key = Fernet.generate_key()
        self.master_key_path.write_bytes(key)

        return Fernet(key)

    # -------------------------
    # Public operations
    # -------------------------

    def init(self) -> dict[str, Any]:
        """
        Initialize an empty PII store file if needed.
        """
        store = self._read_store()

        if "schema_version" not in store:
            store["schema_version"] = STORE_SCHEMA_VERSION

        if "cases" not in store:
            store["cases"] = {}

        self._write_store(store)

        return {
            "store_path": str(self.store_path),
            "master_key_path": str(self.master_key_path),
            "cases": len(store.get("cases", {})),
        }

    def add_or_update(
        self,
        case_id: str,
        pii: dict[str, Any],
        allow_reuse_case: bool = False,
    ) -> dict[str, Any]:
        """
        Encrypt and store PII for a pseudonymous case ID.

        If the case already exists and is active, it is updated.
        If the case was erased, reuse is blocked unless allow_reuse_case=True.
        """
        if not case_id:
            raise ValueError("case_id is required.")

        if not isinstance(pii, dict):
            raise ValueError("PII must be a JSON object / Python dict.")

        store = self._read_store()
        existing = store["cases"].get(case_id)

        if existing and existing.get("status") == "erased" and not allow_reuse_case:
            raise ValueError(
                f"Case '{case_id}' was erased. "
                "Erasure should be final. Use a new case ID or pass "
                "allow_reuse_case only for testing."
            )

        # Generate a fresh per-case key.
        case_key = Fernet.generate_key()
        case_fernet = Fernet(case_key)

        encrypted_pii = case_fernet.encrypt(_canonical_bytes(pii)).decode("utf-8")
        encrypted_case_key = self._master_fernet.encrypt(case_key).decode("utf-8")

        now = _now_iso()

        if existing and existing.get("status") == "active":
            created_at = existing.get("created_at", now)
        else:
            created_at = now

        record = {
            "case_id": case_id,
            "status": "active",
            "created_at": created_at,
            "updated_at": now,
            "encrypted_case_key": encrypted_case_key,
            "encrypted_pii": encrypted_pii,
        }

        store["cases"][case_id] = record
        self._write_store(store)

        return {
            "case_id": case_id,
            "status": "active",
            "created_at": created_at,
            "updated_at": now,
        }

    def get(self, case_id: str) -> dict[str, Any]:
        """
        Decrypt and return PII for a case ID.
        """
        store = self._read_store()
        record = store["cases"].get(case_id)

        if not record:
            raise KeyError(f"No PII record found for case '{case_id}'.")

        if record.get("status") != "active":
            raise PermissionError(
                f"Case '{case_id}' is not active. "
                f"Status: {record.get('status')}"
            )

        encrypted_case_key = record.get("encrypted_case_key")
        encrypted_pii = record.get("encrypted_pii")

        if not encrypted_case_key or not encrypted_pii:
            raise ValueError(f"Case '{case_id}' is missing encrypted material.")

        try:
            case_key = self._master_fernet.decrypt(
                encrypted_case_key.encode("utf-8")
            )
            case_fernet = Fernet(case_key)
            pii_bytes = case_fernet.decrypt(encrypted_pii.encode("utf-8"))
            pii = json.loads(pii_bytes.decode("utf-8"))
        except InvalidToken as exc:
            raise ValueError(
                f"Unable to decrypt case '{case_id}'. "
                "Wrong master key or corrupted record."
            ) from exc

        return {
            "case_id": case_id,
            "status": "active",
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "pii": pii,
        }

    def erase(self, case_id: str, hard: bool = False) -> dict[str, Any]:
        """
        Erase PII for a case.

        Default mode:
            Leaves a tombstone saying the case was erased.

        Hard mode:
            Removes the case entry completely from the PII store.
        """
        store = self._read_store()
        record = store["cases"].get(case_id)

        if not record:
            return {
                "case_id": case_id,
                "status": "not_found",
            }

        if hard:
            del store["cases"][case_id]
            self._write_store(store)

            return {
                "case_id": case_id,
                "status": "hard_erased",
            }

        store["cases"][case_id] = {
            "case_id": case_id,
            "status": "erased",
            "erased_at": _now_iso(),
            "encrypted_case_key": None,
            "encrypted_pii": None,
        }

        self._write_store(store)

        return {
            "case_id": case_id,
            "status": "erased",
        }

    def list_cases(self) -> dict[str, Any]:
        """
        List case metadata without decrypting PII.
        """
        store = self._read_store()

        cases = []

        for case_id, record in store["cases"].items():
            cases.append(
                {
                    "case_id": case_id,
                    "status": record.get("status"),
                    "created_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                    "erased_at": record.get("erased_at"),
                }
            )

        return {
            "store_path": str(self.store_path),
            "total_cases": len(cases),
            "cases": cases,
        }

    def check(self) -> dict[str, Any]:
        """
        Check which active cases can still be decrypted.
        """
        store = self._read_store()

        active = []
        erased = []
        unreadable = []

        for case_id, record in store["cases"].items():
            status = record.get("status")

            if status == "active":
                try:
                    self.get(case_id)
                    active.append(case_id)
                except Exception:
                    unreadable.append(case_id)

            elif status == "erased":
                erased.append(case_id)

        return {
            "ok": len(unreadable) == 0,
            "active_count": len(active),
            "erased_count": len(erased),
            "unreadable_count": len(unreadable),
            "active_cases": active,
            "erased_cases": erased,
            "unreadable_cases": unreadable,
        }


# -------------------------
# Command line interface
# -------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HaqSetu encrypted, erasable PII store."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize the PII store.")

    add_parser = subparsers.add_parser("add", help="Add or update encrypted PII.")
    add_parser.add_argument("--case", required=True, help="Pseudonymous case ID.")
    add_parser.add_argument("--pii", default=None, help="JSON object as text.")
    add_parser.add_argument("--pii-file", default=None, help="Path to JSON file.")
    add_parser.add_argument(
        "--allow-reuse-case",
        action="store_true",
        help="Allow adding to an erased case ID. Use only for testing.",
    )

    get_parser = subparsers.add_parser("get", help="Decrypt and show PII.")
    get_parser.add_argument("--case", required=True)

    erase_parser = subparsers.add_parser("erase", help="Erase PII for a case.")
    erase_parser.add_argument("--case", required=True)
    erase_parser.add_argument(
        "--hard",
        action="store_true",
        help="Remove the tombstone completely.",
    )

    subparsers.add_parser("list", help="List case metadata without PII.")

    subparsers.add_parser("check", help="Check active records can be decrypted.")

    args = parser.parse_args()

    try:
        store = PIIStore()

        if args.command == "init":
            result = store.init()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if args.command == "add":
            if args.pii and args.pii_file:
                print("Use only one of --pii or --pii-file.", file=sys.stderr)
                sys.exit(2)

            if args.pii_file:
                pii_text = Path(args.pii_file).read_text(encoding="utf-8")
            elif args.pii:
                pii_text = args.pii
            else:
                print("Provide --pii or --pii-file.", file=sys.stderr)
                sys.exit(2)

            try:
                pii = json.loads(pii_text)
            except json.JSONDecodeError as exc:
                print(f"Invalid JSON PII: {exc}", file=sys.stderr)
                sys.exit(2)

            result = store.add_or_update(
                case_id=args.case,
                pii=pii,
                allow_reuse_case=args.allow_reuse_case,
            )

            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if args.command == "get":
            result = store.get(args.case)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if args.command == "erase":
            result = store.erase(args.case, hard=args.hard)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if args.command == "list":
            result = store.list_cases()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if args.command == "check":
            result = store.check()
            print(json.dumps(result, ensure_ascii=False, indent=2))

            if not result["ok"]:
                sys.exit(1)

            return

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()