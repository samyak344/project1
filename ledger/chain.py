from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER_DIR = PROJECT_ROOT / "ledger"

SCHEMA_VERSION = 1
GENESIS_PREV_HASH = "GENESIS"

CORE_FIELDS = [
    "schema_version",
    "seq",
    "timestamp",
    "actor",
    "role",
    "case_id",
    "event_type",
    "data",
    "prev_hash",
]

# The ledger must not store direct personal identifiers.
# It should store case IDs, event codes, hashes, and references only.
FORBIDDEN_KEY_MARKERS = {
    "aadhaar",
    "uid",
    "name",
    "dob",
    "date_of_birth",
    "birth",
    "phone",
    "mobile",
    "address",
    "bank_account",
    "account_number",
}


def _canonical_bytes(obj: dict[str, Any]) -> bytes:
    """
    Convert a dictionary into one stable byte sequence.

    This is important because hashing and signing must always see
    the exact same ordering and format.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_no_pii(obj: Any, path: str = "data") -> None:
    """
    Reject dictionaries whose keys look like personal data.

    The ledger should store pseudonymous case IDs and event codes,
    not direct identifiers.
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_text = str(key).lower()

            for marker in FORBIDDEN_KEY_MARKERS:
                if marker in key_text:
                    raise ValueError(
                        f"Refusing to store possible PII in ledger: "
                        f"{path}.{key} contains marker '{marker}'."
                    )

            ensure_no_pii(value, path=f"{path}.{key}")

    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            ensure_no_pii(item, path=f"{path}[{i}]")


def _safe_filename(text: str) -> str:
    """
    Make an actor name safe for use as a filename.
    """
    return "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in text
    )


class Ledger:
    def __init__(
        self,
        ledger_path: Path | str | None = None,
        keys_dir: Path | str | None = None,
    ) -> None:
        if ledger_path is None:
            ledger_path = DEFAULT_LEDGER_DIR / "chain.jsonl"

        if keys_dir is None:
            keys_dir = DEFAULT_LEDGER_DIR / "keys"

        self.ledger_path = Path(ledger_path)
        self.keys_dir = Path(keys_dir)

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.keys_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # Key management
    # -------------------------

    def private_key_path(self, actor: str) -> Path:
        return self.keys_dir / f"{_safe_filename(actor)}.private.pem"

    def public_key_path(self, actor: str) -> Path:
        return self.keys_dir / f"{_safe_filename(actor)}.public.pem"

    def generate_actor_keys(self, actor: str, overwrite: bool = False) -> dict[str, str]:
        """
        Generate an Ed25519 key pair for an actor.

        The private key signs ledger events.
        The public key verifies signatures.
        """
        private_path = self.private_key_path(actor)
        public_path = self.public_key_path(actor)

        if private_path.exists() and public_path.exists() and not overwrite:
            return {
                "actor": actor,
                "private_key": str(private_path),
                "public_key": str(public_path),
                "status": "already_exists",
            }

        private_key = Ed25519PrivateKey.generate()

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

        private_path.write_bytes(private_pem)
        public_path.write_bytes(public_pem)

        return {
            "actor": actor,
            "private_key": str(private_path),
            "public_key": str(public_path),
            "status": "generated",
        }

    def _load_private_key(self, actor: str):
        private_path = self.private_key_path(actor)

        if not private_path.exists():
            raise FileNotFoundError(
                f"No private key found for actor '{actor}'. "
                f"Run: python ledger/chain.py generate-keys --actor {actor}"
            )

        return serialization.load_pem_private_key(
            private_path.read_bytes(),
            password=None,
        )

    def _load_public_key(self, actor: str):
        public_path = self.public_key_path(actor)

        if not public_path.exists():
            raise FileNotFoundError(
                f"No public key found for actor '{actor}'. "
                f"Run: python ledger/chain.py generate-keys --actor {actor}"
            )

        return serialization.load_pem_public_key(public_path.read_bytes())

    # -------------------------
    # Core ledger operations
    # -------------------------

    def read_events(self) -> list[dict[str, Any]]:
        if not self.ledger_path.exists():
            return []

        events = []

        with self.ledger_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(json.loads(line))

        return events

    def last_record(self) -> dict[str, Any] | None:
        events = self.read_events()
        if not events:
            return None
        return events[-1]

    def append_event(
        self,
        actor: str,
        case_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        role: str | None = None,
    ) -> dict[str, Any]:
        """
        Append one signed event to the ledger.

        The event is linked to the previous event by prev_hash.
        """
        if data is None:
            data = {}

        if not isinstance(data, dict):
            raise ValueError("Event data must be a JSON object / Python dict.")

        if not case_id:
            raise ValueError("case_id is required.")

        if not actor:
            raise ValueError("actor is required.")

        if not event_type:
            raise ValueError("event_type is required.")

        ensure_no_pii(data)
        ensure_no_pii(case_id, path="case_id")

        last = self.last_record()

        if last is None:
            seq = 0
            prev_hash = GENESIS_PREV_HASH
        else:
            seq = int(last["seq"]) + 1
            prev_hash = last["hash"]

        core = {
            "schema_version": SCHEMA_VERSION,
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "role": role,
            "case_id": case_id,
            "event_type": event_type,
            "data": data,
            "prev_hash": prev_hash,
        }

        core_bytes = _canonical_bytes(core)
        record_hash = _sha256_hex(core_bytes)

        private_key = self._load_private_key(actor)
        signature = private_key.sign(core_bytes)

        full_record = {
            **core,
            "hash": record_hash,
            "signature": signature.hex(),
        }

        with self.ledger_path.open("a", encoding="utf-8") as f:
            json.dump(full_record, f, ensure_ascii=False, sort_keys=True)
            f.write("\n")

        return full_record

    def ensure_initialized(self) -> dict[str, Any]:
        """
        Create system keys if needed and append the genesis record
        if the ledger is empty.
        """
        self.generate_actor_keys("system", overwrite=False)

        if self.last_record() is None:
            self.append_event(
                actor="system",
                role="system",
                case_id="ledger",
                event_type="genesis",
                data={"note": "HaqSetu ledger started"},
            )

        return {
            "ledger_path": str(self.ledger_path),
            "keys_dir": str(self.keys_dir),
            "records": len(self.read_events()),
        }

    # -------------------------
    # Verification
    # -------------------------

    def verify_signature_for_actor(
        self,
        actor: str,
        core_bytes: bytes,
        signature_hex: str,
    ) -> bool:
        try:
            public_key = self._load_public_key(actor)
            signature = bytes.fromhex(signature_hex)
            public_key.verify(signature, core_bytes)
            return True
        except Exception:
            return False

    def quick_check(self) -> dict[str, Any]:
        """
        Verify:
        - sequence numbers,
        - prev_hash links,
        - record hashes,
        - signatures.
        """
        events = self.read_events()
        errors: list[str] = []
        expected_prev_hash = GENESIS_PREV_HASH

        for i, record in enumerate(events):
            label = f"record {i} ({record.get('event_type', 'unknown')})"

            if record.get("seq") != i:
                errors.append(f"{label}: bad sequence number")

            if record.get("prev_hash") != expected_prev_hash:
                errors.append(f"{label}: broken prev_hash link")

            core = {field: record.get(field) for field in CORE_FIELDS}
            core_bytes = _canonical_bytes(core)

            recomputed_hash = _sha256_hex(core_bytes)

            if record.get("hash") != recomputed_hash:
                errors.append(f"{label}: hash mismatch")
            else:
                actor = record.get("actor", "")
                signature = record.get("signature", "")

                if not self.verify_signature_for_actor(actor, core_bytes, signature):
                    errors.append(f"{label}: signature invalid")

            expected_prev_hash = record.get("hash", "")

        return {
            "ok": len(errors) == 0,
            "records": len(events),
            "errors": errors,
        }


# -------------------------
# Command line interface
# -------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="HaqSetu append-only signed ledger.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Initialize ledger and genesis record.")

    keys_parser = subparsers.add_parser(
        "generate-keys",
        help="Generate keys for an actor.",
    )
    keys_parser.add_argument("--actor", required=True)
    keys_parser.add_argument("--overwrite", action="store_true")

    add_parser = subparsers.add_parser("add", help="Append an event to the ledger.")
    add_parser.add_argument("--actor", required=True)
    add_parser.add_argument("--role", default=None)
    add_parser.add_argument("--case", required=True, help="Pseudonymous case ID.")
    add_parser.add_argument("--type", required=True, dest="event_type")
    add_parser.add_argument("--data", default=None, help="JSON object as text.")
    add_parser.add_argument("--data-file", default=None, help="Path to JSON file.")

    show_parser = subparsers.add_parser("show", help="Show ledger events.")
    show_parser.add_argument("--limit", type=int, default=None)
    show_parser.add_argument("--pretty", action="store_true")

    subparsers.add_parser("check", help="Quickly verify chain hashes and signatures.")

    args = parser.parse_args()

    ledger = Ledger()

    try:
        if args.command == "init":
            result = ledger.ensure_initialized()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if args.command == "generate-keys":
            result = ledger.generate_actor_keys(args.actor, overwrite=args.overwrite)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if args.command == "add":
            ledger.ensure_initialized()

            if args.data and args.data_file:
                print("Use only one of --data or --data-file.", file=sys.stderr)
                sys.exit(2)

            if args.data_file:
                data_text = Path(args.data_file).read_text(encoding="utf-8")
            elif args.data:
                data_text = args.data
            else:
                data_text = "{}"

            try:
                data = json.loads(data_text)
            except json.JSONDecodeError as exc:
                print(f"Invalid JSON data: {exc}", file=sys.stderr)
                sys.exit(2)

            record = ledger.append_event(
                actor=args.actor,
                role=args.role,
                case_id=args.case,
                event_type=args.event_type,
                data=data,
            )

            print(json.dumps(record, ensure_ascii=False, indent=2))
            return

        if args.command == "show":
            events = ledger.read_events()

            if args.limit:
                events = events[-args.limit :]

            for event in events:
                if args.pretty:
                    print(json.dumps(event, ensure_ascii=False, indent=2))
                else:
                    print(json.dumps(event, ensure_ascii=False))

            return

        if args.command == "check":
            result = ledger.quick_check()
            print(json.dumps(result, ensure_ascii=False, indent=2))

            if not result["ok"]:
                sys.exit(1)

            return

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()