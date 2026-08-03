from __future__ import annotations

import argparse
import os
import shutil
import time
import uuid
from pathlib import Path

from .database import GatewayDatabase


PROJECT_ROOT = Path(__file__).resolve().parent


def restore_backup(data_root: Path, source: Path) -> tuple[Path, Path | None]:
    data_root = data_root.resolve()
    source = source.resolve()
    database = GatewayDatabase(data_root / "gateway.db")
    database.verify_backup(source)
    if source == database.path.resolve():
        raise ValueError("Backup source must be different from the active gateway database")

    data_root.mkdir(parents=True, exist_ok=True)
    safety_backup = None
    if database.path.is_file():
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        safety_backup = database.backup(data_root / "backups" / f"before-restore-{stamp}.db")

    temporary = data_root / f".gateway-restore-{uuid.uuid4().hex}.db"
    try:
        shutil.copy2(source, temporary)
        database.verify_backup(temporary)
        os.replace(temporary, database.path)
        database.path.with_name(database.path.name + "-wal").unlink(missing_ok=True)
        database.path.with_name(database.path.name + "-shm").unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
    return database.path, safety_backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage a Grapyth AI Gateway installation")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.environ.get(
                "GRAPYTH_GATEWAY_DATA_ROOT",
                PROJECT_ROOT / "var" / "gateway",
            )
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="Create a consistent gateway SQLite backup")
    backup.add_argument("--target", type=Path)
    verify = commands.add_parser("verify", help="Verify a gateway SQLite backup")
    verify.add_argument("--source", type=Path, required=True)
    restore = commands.add_parser("restore", help="Restore a verified gateway SQLite backup")
    restore.add_argument("--source", type=Path, required=True)
    restore.add_argument(
        "--confirm-service-stopped",
        action="store_true",
        help="Confirm that the gateway service is stopped before replacing its database",
    )
    args = parser.parse_args()

    data_root = args.data_root.resolve()
    database = GatewayDatabase(data_root / "gateway.db")
    if args.command == "backup":
        target = args.target or data_root / "backups" / time.strftime("gateway-%Y%m%d-%H%M%S.db", time.gmtime())
        print(database.backup(target))
        return
    if args.command == "verify":
        database.verify_backup(args.source)
        print(args.source.resolve())
        return
    if not args.confirm_service_stopped:
        parser.error("restore requires --confirm-service-stopped")
    restored, safety_backup = restore_backup(data_root, args.source)
    print(restored)
    if safety_backup:
        print(f"Safety backup: {safety_backup}")


if __name__ == "__main__":
    main()
