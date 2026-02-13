#!/usr/bin/env python3
"""Migrate existing single-user data to multi-user database.

Imports:
  1. API keys from .env file -> user_api_keys table (Fernet encrypted)
  2. Result JSON files from results/ -> benchmark_runs table
  3. config.yaml provider/model setup -> preserved as-is (system config)

Usage:
    python migrate_to_multiuser.py                    # Interactive
    python migrate_to_multiuser.py --admin-user admin@example.com
    python migrate_to_multiuser.py --dry-run           # Preview without writing
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import aiosqlite
import yaml
from dotenv import dotenv_values

# Re-use the project's keyvault for encryption consistency
from keyvault import vault


# Paths (match Docker volume mounts and local dev layout)
BASE_DIR = Path(os.getenv("APP_DIR", Path(__file__).parent))
ENV_PATH = BASE_DIR / ".env"
RESULTS_DIR = Path(os.getenv("RESULTS_DIR", BASE_DIR / "results"))
CONFIG_PATH = BASE_DIR / "config.yaml"

# DB path matches db.py
DB_PATH = Path(os.getenv("DB_PATH", BASE_DIR / "data" / "benchmark_studio.db"))


async def get_admin_user(db_conn: aiosqlite.Connection, email: str = None) -> dict:
    """Find or identify the admin user to own migrated data."""
    if email:
        cursor = await db_conn.execute(
            "SELECT id, email, role FROM users WHERE email = ? COLLATE NOCASE", (email,)
        )
        row = await cursor.fetchone()
        if not row:
            print(f"ERROR: User '{email}' not found in database.")
            print("Available users:")
            cursor2 = await db_conn.execute("SELECT email, role FROM users")
            async for r in cursor2:
                print(f"  - {r[0]} ({r[1]})")
            sys.exit(1)
        return {"id": row[0], "email": row[1], "role": row[2]}

    # Find first admin
    cursor = await db_conn.execute(
        "SELECT id, email, role FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1"
    )
    row = await cursor.fetchone()
    if not row:
        print("ERROR: No admin user found. Register an admin first via the web UI.")
        sys.exit(1)
    return {"id": row[0], "email": row[1], "role": row[2]}


async def migrate_api_keys(db_conn: aiosqlite.Connection, admin_id: str, dry_run: bool) -> int:
    """Import API keys from .env into user_api_keys table."""
    if not ENV_PATH.exists():
        print("  No .env file found, skipping API key migration.")
        return 0

    if not CONFIG_PATH.exists():
        print("  No config.yaml found, skipping API key migration.")
        return 0

    # Load config to map env var names to provider keys
    with open(CONFIG_PATH) as f:
        config = yaml.safe_load(f)

    # Build mapping: env_var_name -> provider_key
    env_to_provider = {}
    for prov_key, prov_cfg in config.get("providers", {}).items():
        env_var = prov_cfg.get("api_key_env")
        if env_var:
            env_to_provider[env_var] = prov_key

    # Also handle inline api_key values in config
    inline_keys = {}
    for prov_key, prov_cfg in config.get("providers", {}).items():
        if prov_cfg.get("api_key") and prov_cfg["api_key"] != "not-needed":
            inline_keys[prov_key] = prov_cfg["api_key"]

    # Parse .env file
    env_values = dotenv_values(ENV_PATH)

    migrated = 0

    # Migrate keys referenced by env vars
    for env_var, provider_key in env_to_provider.items():
        value = env_values.get(env_var, "")
        if not value or value.startswith("your-"):
            print(f"  SKIP: {env_var} (not set or placeholder)")
            continue

        # Check if already migrated (idempotent)
        cursor = await db_conn.execute(
            "SELECT id FROM user_api_keys WHERE user_id = ? AND provider_key = ?",
            (admin_id, provider_key),
        )
        existing = await cursor.fetchone()
        if existing:
            print(f"  SKIP: {provider_key} (already exists for this user)")
            continue

        if dry_run:
            print(f"  WOULD IMPORT: {env_var} -> provider '{provider_key}' for admin user")
        else:
            encrypted = vault.encrypt(value)
            import uuid
            key_id = uuid.uuid4().hex
            await db_conn.execute(
                "INSERT INTO user_api_keys (id, user_id, provider_key, key_name, encrypted_value) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_id, admin_id, provider_key, env_var, encrypted),
            )
            print(f"  IMPORTED: {env_var} -> provider '{provider_key}'")
        migrated += 1

    # Handle inline keys from config
    for provider_key, key_value in inline_keys.items():
        cursor = await db_conn.execute(
            "SELECT id FROM user_api_keys WHERE user_id = ? AND provider_key = ?",
            (admin_id, provider_key),
        )
        existing = await cursor.fetchone()
        if existing:
            print(f"  SKIP: {provider_key} inline key (already exists)")
            continue

        if dry_run:
            print(f"  WOULD IMPORT: inline key for provider '{provider_key}'")
        else:
            encrypted = vault.encrypt(key_value)
            import uuid
            key_id = uuid.uuid4().hex
            await db_conn.execute(
                "INSERT INTO user_api_keys (id, user_id, provider_key, key_name, encrypted_value) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_id, admin_id, provider_key, f"{provider_key.upper()}_API_KEY", encrypted),
            )
            print(f"  IMPORTED: inline key for provider '{provider_key}'")
        migrated += 1

    if not dry_run and migrated > 0:
        await db_conn.commit()

    return migrated


async def migrate_results(db_conn: aiosqlite.Connection, admin_id: str, dry_run: bool) -> int:
    """Import existing results/ JSON files into benchmark_runs table."""
    if not RESULTS_DIR.exists():
        print("  No results/ directory found, skipping results migration.")
        return 0

    json_files = sorted(RESULTS_DIR.glob("benchmark_*.json"))
    if not json_files:
        print("  No benchmark result files found.")
        return 0

    migrated = 0
    for filepath in json_files:
        try:
            data = json.loads(filepath.read_text())
        except (json.JSONDecodeError, IOError) as e:
            print(f"  SKIP: {filepath.name} (parse error: {e})")
            continue

        # Use the timestamp from the file as the benchmark timestamp
        timestamp = data.get("timestamp", "")
        if not timestamp:
            print(f"  SKIP: {filepath.name} (no timestamp field)")
            continue

        # Check if already migrated (idempotent -- match by user + timestamp)
        cursor = await db_conn.execute(
            "SELECT id FROM benchmark_runs WHERE user_id = ? AND timestamp = ?",
            (admin_id, timestamp),
        )
        existing = await cursor.fetchone()
        if existing:
            print(f"  SKIP: {filepath.name} (already migrated)")
            continue

        prompt = data.get("prompt", "")
        context_tiers = json.dumps(data.get("context_tiers", [0]))
        results_json = json.dumps(data.get("results", []))
        metadata = json.dumps({"source_file": filepath.name, "schema_version": data.get("schema_version", 1)})

        if dry_run:
            result_count = len(data.get("results", []))
            print(f"  WOULD IMPORT: {filepath.name} ({result_count} model results)")
        else:
            import uuid
            run_id = uuid.uuid4().hex
            await db_conn.execute(
                "INSERT INTO benchmark_runs (id, user_id, timestamp, prompt, context_tiers, results_json, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, admin_id, timestamp, prompt, context_tiers, results_json, metadata),
            )
            print(f"  IMPORTED: {filepath.name}")
        migrated += 1

    if not dry_run and migrated > 0:
        await db_conn.commit()

    return migrated


async def main():
    parser = argparse.ArgumentParser(description="Migrate single-user data to multi-user database")
    parser.add_argument(
        "--admin-user",
        help="Email of the admin user to own migrated data (default: first admin)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database",
    )
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        print("Run the app first to initialize the database, then register an admin user.")
        sys.exit(1)

    print(f"{'DRY RUN - ' if args.dry_run else ''}Migration: Single-user -> Multi-user")
    print(f"  Database: {DB_PATH}")
    print(f"  .env:     {ENV_PATH}")
    print(f"  Results:  {RESULTS_DIR}")
    print(f"  Config:   {CONFIG_PATH}")
    print()

    async with aiosqlite.connect(str(DB_PATH)) as db_conn:
        db_conn.row_factory = aiosqlite.Row

        admin = await get_admin_user(db_conn, args.admin_user)
        print(f"Target user: {admin['email']} (id={admin['id']}, role={admin['role']})")
        print()

        # Migrate API keys
        print("--- API Keys ---")
        key_count = await migrate_api_keys(db_conn, admin["id"], args.dry_run)
        print(f"  Total: {key_count} keys {'would be ' if args.dry_run else ''}migrated")
        print()

        # Migrate results
        print("--- Benchmark Results ---")
        result_count = await migrate_results(db_conn, admin["id"], args.dry_run)
        print(f"  Total: {result_count} result files {'would be ' if args.dry_run else ''}migrated")
        print()

    if args.dry_run:
        print("DRY RUN complete. Re-run without --dry-run to apply changes.")
    else:
        print("Migration complete!")
        print()
        print("Next steps:")
        print("  1. Verify data in the web UI (login as the admin user)")
        print("  2. Optionally rename results/ to results.bak/ (keep as backup)")
        print("  3. Optionally rename .env to .env.bak (keys are now in the database)")


if __name__ == "__main__":
    asyncio.run(main())
