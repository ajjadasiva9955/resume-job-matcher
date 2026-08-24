#!/usr/bin/env python3
"""
migrate_to_supabase.py — Safe, Non-Destructive Migration Script for SkillBridge.AI
Migrates local SQLite database (13 tables) and local resume files to Supabase (PostgreSQL + Supabase Storage).

Features:
- --dry-run: Inspects SQLite records, local resume files, and expected storage keys without modifying anything.
- --verify-only: Compares SQLite vs PostgreSQL row counts, storage objects, and data integrity.
- --table <name>: Migrates a single specific table.
- Normal mode: Performs safe, idempotent migration with upserts, sequence synchronization, and storage uploads.

SAFETY GUARANTEES:
- NEVER modifies or deletes instance/skillbridge.db
- NEVER modifies or deletes uploads/ directory or files
- NEVER prints plain secrets, passwords, tokens, or encryption keys
- Rerunnable and idempotent (zero duplicates)
"""

import os
import sys
import re
import io
import mimetypes
import hashlib
import sqlite3
import argparse
from typing import Dict, List, Tuple, Any, Optional
from dotenv import load_dotenv

# Ensure application root is in path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"))

import auth_db
import storage_manager

DB_PATH = os.path.join(BASE_DIR, "instance", "skillbridge.db")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
STORAGE_BUCKET = storage_manager.get_storage_bucket()

# 13 Application Tables in topological foreign-key dependency order
MIGRATION_TABLES = [
    "users",
    "password_reset_tokens",
    "user_api_keys",
    "user_resumes",
    "user_sessions",
    "saved_jobs",
    "applied_jobs",
    "job_search_results",
    "api_key_history",
    "api_usage_logs",
    "user_search_cooldown",
    "ats_analyses",
    "course_topic_progress",
]

# Exact column definitions per table
TABLE_COLUMNS = {
    "users": [
        "id", "username", "email", "password_hash", "created_at", "updated_at", "last_login_at", "is_active"
    ],
    "password_reset_tokens": [
        "id", "user_id", "token_hash", "expires_at", "used_at", "created_at"
    ],
    "user_api_keys": [
        "id", "user_id", "serpapi_key_encrypted", "gemini_api_key_encrypted", "created_at", "updated_at"
    ],
    "user_resumes": [
        "id", "user_id", "original_filename", "stored_filename", "file_path", "file_type",
        "file_size", "uploaded_at", "processing_status", "extracted_data_json", "is_current"
    ],
    "user_sessions": [
        "id", "user_id", "token_hash", "expires_at", "created_at", "last_used_at", "user_agent", "ip_address"
    ],
    "saved_jobs": [
        "id", "user_id", "job_id", "job_title", "company", "company_logo", "location",
        "employment_type", "experience", "salary", "match_score", "posted_time",
        "application_url", "job_description", "source", "openings", "saved_at"
    ],
    "applied_jobs": [
        "id", "user_id", "job_id", "job_title", "company", "company_logo", "location",
        "employment_type", "experience", "salary", "match_score", "posted_time",
        "application_url", "job_description", "source", "openings", "applied_at"
    ],
    "job_search_results": [
        "id", "user_id", "search_id", "is_current", "skills_json", "roles_json",
        "role_matches_json", "missing_skills_json", "market_insights_json", "jobs_data_json",
        "total_jobs", "search_version", "created_at", "updated_at"
    ],
    "api_key_history": [
        "id", "user_id", "service", "key_fingerprint", "masked_key", "added_at", "last_used_at",
        "status", "last_known_usage", "last_known_limit", "last_known_hourly_usage",
        "last_known_hourly_limit", "remaining_searches", "plan_name", "renewal_date",
        "last_error_category", "is_current", "details_json"
    ],
    "api_usage_logs": [
        "id", "user_id", "service", "feature", "model", "key_fingerprint", "timestamp",
        "success", "error_category", "error_message", "prompt_tokens", "candidates_tokens",
        "total_tokens", "retry_after_seconds"
    ],
    "user_search_cooldown": [
        "user_id", "last_search_started_at", "cooldown_until", "search_in_progress", "search_started_timestamp"
    ],
    "ats_analyses": [
        "id", "user_id", "filename", "stored_filename", "file_path", "file_type", "file_size",
        "uploaded_at", "analyzed_at", "final_score", "ats_readability_score", "content_quality_score",
        "skills_score", "experience_projects_score", "completeness_score", "grammar_consistency_score",
        "score_message", "score_status", "primary_domain", "parsed_sections_json", "detected_skills_json",
        "industry_terms_json", "strengths_json", "problems_detected_json", "missing_sections_json",
        "weak_bullets_json", "recommendations_json", "consistency_findings_json", "analysis_json",
        "is_current", "quantification_score"
    ],
    "course_topic_progress": [
        "id", "user_id", "course_id", "topic_id", "completed", "completed_at", "last_watched_at",
        "created_at", "updated_at"
    ],
}


def calculate_sha256(file_path: str) -> Optional[str]:
    """Calculates SHA256 checksum of a file on disk."""
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return None
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_local_source_snapshot() -> Dict[str, Any]:
    """Captures file size and SHA256 hash for local SQLite DB and all resume uploads."""
    snapshot = {
        "db": {
            "path": DB_PATH,
            "exists": os.path.exists(DB_PATH),
            "size": os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0,
            "sha256": calculate_sha256(DB_PATH),
        },
        "files": {},
    }
    if os.path.exists(UPLOADS_DIR):
        for root, _, files in os.walk(UPLOADS_DIR):
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, BASE_DIR).replace("\\", "/")
                snapshot["files"][rel_path] = {
                    "size": os.path.getsize(full_path),
                    "sha256": calculate_sha256(full_path),
                }
    return snapshot


def verify_local_source_integrity(before_snapshot: Dict[str, Any]) -> bool:
    """Verifies that local SQLite database and uploads are 100% byte-for-byte identical to pre-migration state."""
    after_snapshot = get_local_source_snapshot()
    
    # Check DB
    if before_snapshot["db"]["size"] != after_snapshot["db"]["size"]:
        print(f"[INTEGRITY ERROR] SQLite DB size changed: {before_snapshot['db']['size']} -> {after_snapshot['db']['size']}")
        return False
    if before_snapshot["db"]["sha256"] != after_snapshot["db"]["sha256"]:
        print("[INTEGRITY ERROR] SQLite DB SHA256 hash changed!")
        return False

    # Check uploads
    if len(before_snapshot["files"]) != len(after_snapshot["files"]):
        print(f"[INTEGRITY ERROR] Local upload file count changed: {len(before_snapshot['files'])} -> {len(after_snapshot['files'])}")
        return False

    for path, meta in before_snapshot["files"].items():
        if path not in after_snapshot["files"]:
            print(f"[INTEGRITY ERROR] Missing upload file: {path}")
            return False
        if meta["size"] != after_snapshot["files"][path]["size"]:
            print(f"[INTEGRITY ERROR] File size changed for {path}")
            return False
        if meta["sha256"] != after_snapshot["files"][path]["sha256"]:
            print(f"[INTEGRITY ERROR] SHA256 checksum changed for {path}")
            return False

    return True


def get_sqlite_row_counts(db_path: str = DB_PATH) -> Dict[str, int]:
    """Returns row count for all 13 tables in local SQLite database."""
    counts = {}
    if not os.path.exists(db_path):
        return counts
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    for table in MIGRATION_TABLES:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
        except Exception:
            counts[table] = 0
    con.close()
    return counts


def get_local_resume_files() -> List[Dict[str, Any]]:
    """Discovers all persistent resume files in uploads/ matching user_<id>/<filename>."""
    results = []
    if not os.path.exists(UPLOADS_DIR):
        return results

    for entry in sorted(os.listdir(UPLOADS_DIR)):
        user_dir = os.path.join(UPLOADS_DIR, entry)
        if os.path.isdir(user_dir) and entry.startswith("user_"):
            user_id_str = entry.split("user_")[1]
            try:
                user_id = int(user_id_str)
            except ValueError:
                user_id = user_id_str

            for fname in sorted(os.listdir(user_dir)):
                fpath = os.path.join(user_dir, fname)
                if os.path.isfile(fpath) and fname in ("main_resume.pdf", "ats_resume.pdf", "ats_resume.docx", "ats_resume.doc"):
                    storage_key = f"user_{user_id}/{fname}"
                    results.append({
                        "user_id": user_id,
                        "filename": fname,
                        "local_path": fpath,
                        "storage_key": storage_key,
                        "size": os.path.getsize(fpath),
                        "sha256": calculate_sha256(fpath),
                    })
    return results


def run_dry_run():
    """Executes Dry Run and prints inspection report without modifying anything."""
    print("=" * 60)
    print("SKILLBRIDGE.AI — SUPABASE MIGRATION (DRY RUN)")
    print("=" * 60)
    print("Source Database : instance/skillbridge.db")
    print("Source Uploads  : uploads/")
    print(f"Target Bucket   : {STORAGE_BUCKET} (private)")
    print("-" * 60)

    if not os.path.exists(DB_PATH):
        print(f"[FATAL] Source database not found at {DB_PATH}")
        sys.exit(1)

    counts = get_sqlite_row_counts(DB_PATH)
    print("Source SQLite Table Counts:")
    for table in MIGRATION_TABLES:
        cnt = counts.get(table, 0)
        print(f"  {table:25}: {cnt:4} rows")

    files = get_local_resume_files()
    print(f"\nDiscovered Resume Files ({len(files)} total):")
    for f in files:
        print(f"  {f['local_path']:40} -> {f['storage_key']} ({f['size']} bytes)")

    print("\nExpected Storage Keys:")
    for f in files:
        print(f"  {f['storage_key']}")

    print("\nDry Run Summary:")
    print(f"  Total Tables Planned: {len(MIGRATION_TABLES)}")
    print(f"  Total Records       : {sum(counts.values())}")
    print(f"  Total Resume Files  : {len(files)}")
    print("=" * 60)
    print("DRY RUN STATUS: PASS (Zero modifications made)")
    print("=" * 60)
    return counts, files


def build_postgres_upsert_query(table: str) -> str:
    """Generates idempotent PostgreSQL ON CONFLICT DO UPDATE query for a given table."""
    columns = TABLE_COLUMNS[table]
    cols_joined = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    if table == "user_search_cooldown":
        # PK is user_id
        update_clauses = [f"{col}=EXCLUDED.{col}" for col in columns if col != "user_id"]
        update_str = ", ".join(update_clauses)
        return f"INSERT INTO {table} ({cols_joined}) VALUES ({placeholders}) ON CONFLICT (user_id) DO UPDATE SET {update_str};"
    else:
        # PK is id
        update_clauses = [f"{col}=EXCLUDED.{col}" for col in columns if col != "id"]
        update_str = ", ".join(update_clauses)
        return f"INSERT INTO {table} ({cols_joined}) VALUES ({placeholders}) ON CONFLICT (id) DO UPDATE SET {update_str};"


def migrate_database_table(sqlite_conn: sqlite3.Connection, pg_conn, table: str) -> Tuple[int, int]:
    """
    Migrates a single table from SQLite to PostgreSQL preserving exact primary keys and data.
    Returns (sqlite_count, pg_count).
    """
    columns = TABLE_COLUMNS[table]
    cols_joined = ", ".join(columns)
    
    # Read from SQLite
    s_cur = sqlite_conn.cursor()
    s_cur.execute(f"SELECT {cols_joined} FROM {table} ORDER BY {columns[0]} ASC")
    rows = s_cur.fetchall()
    sqlite_count = len(rows)

    if not rows:
        # Check pg count
        p_cur = pg_conn.cursor()
        p_cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
        res = p_cur.fetchone()
        pg_count = res["cnt"] if isinstance(res, dict) else res[0]
        return sqlite_count, pg_count

    upsert_sql = build_postgres_upsert_query(table)
    
    # Execute batch upsert in PostgreSQL
    p_cur = pg_conn.cursor()
    row_tuples = [tuple(r) for r in rows]
    p_cur.executemany(upsert_sql, row_tuples)
    pg_conn.commit()

    # Reset sequence if table has serial 'id'
    if table != "user_search_cooldown" and "id" in columns:
        try:
            seq_reset_sql = f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    (SELECT MAX(id) FROM {table}) IS NOT NULL
                );
            """
            p_cur.execute(seq_reset_sql)
            pg_conn.commit()
        except Exception as e:
            # Ignore sequence reset error if table does not use default serial sequence
            pass

    # Verify count
    p_cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
    res = p_cur.fetchone()
    pg_count = res["cnt"] if isinstance(res, dict) else res[0]
    return sqlite_count, pg_count


def migrate_resume_files_to_supabase(files: List[Dict[str, Any]]) -> Tuple[int, int, List[str]]:
    """
    Uploads local resume files to Supabase Storage bucket 'resumes'.
    Returns (local_count, uploaded_count, failed_keys).
    """
    if not storage_manager.is_supabase_storage_configured():
        print("[WARNING] Supabase Storage credentials not configured in environment. Skipping cloud upload.")
        return len(files), 0, [f["storage_key"] for f in files]

    # Ensure bucket exists
    storage_manager.ensure_supabase_bucket(STORAGE_BUCKET)

    uploaded_count = 0
    failed = []

    for item in files:
        user_id = item["user_id"]
        fname = item["filename"]
        lpath = item["local_path"]
        skey = item["storage_key"]

        try:
            with open(lpath, "rb") as f:
                content_bytes = f.read()

            resume_type = "ats" if fname.startswith("ats_") else "main"
            res = storage_manager.upload_resume_file(
                user_id=user_id,
                file_data=content_bytes,
                original_filename=fname,
                resume_type=resume_type,
            )
            if res.get("success"):
                uploaded_count += 1
            else:
                failed.append(skey)
        except Exception as e:
            print(f"[STORAGE UPLOAD ERROR] Failed to upload {skey}: {e}")
            failed.append(skey)

    return len(files), uploaded_count, failed


def verify_database_data_values(sqlite_conn: sqlite3.Connection, pg_conn) -> bool:
    """Verifies that representative data values across core tables match exactly between SQLite and PostgreSQL."""
    s_cur = sqlite_conn.cursor()
    p_cur = pg_conn.cursor()

    # 1. Users verification
    s_cur.execute("SELECT id, username, email, password_hash FROM users ORDER BY id")
    s_users = s_cur.fetchall()
    for s_user in s_users:
        uid, uname, uemail, pw_hash = s_user
        p_cur.execute("SELECT id, username, email, password_hash FROM users WHERE id = %s", (uid,))
        p_user = p_cur.fetchone()
        if not p_user:
            print(f"[DATA VERIFY ERROR] User ID {uid} missing in PostgreSQL")
            return False
        if p_user["username"] != uname or p_user["email"] != uemail or p_user["password_hash"] != pw_hash:
            print(f"[DATA VERIFY ERROR] User ID {uid} metadata mismatch")
            return False

    # 2. User API keys verification
    s_cur.execute("SELECT id, user_id, serpapi_key_encrypted, gemini_api_key_encrypted FROM user_api_keys ORDER BY id")
    s_keys = s_cur.fetchall()
    for sk in s_keys:
        kid, uid, serp, gem = sk
        p_cur.execute("SELECT id, user_id, serpapi_key_encrypted, gemini_api_key_encrypted FROM user_api_keys WHERE id = %s", (kid,))
        pk = p_cur.fetchone()
        if not pk or pk["user_id"] != uid or pk["serpapi_key_encrypted"] != serp or pk["gemini_api_key_encrypted"] != gem:
            print(f"[DATA VERIFY ERROR] API Key record {kid} mismatch")
            return False

    # 3. User Resumes verification
    s_cur.execute("SELECT id, user_id, stored_filename, file_size, is_current FROM user_resumes ORDER BY id")
    s_resumes = s_cur.fetchall()
    for sr in s_resumes:
        rid, uid, sfn, fsize, is_curr = sr
        p_cur.execute("SELECT id, user_id, stored_filename, file_size, is_current FROM user_resumes WHERE id = %s", (rid,))
        pr = p_cur.fetchone()
        if not pr or pr["user_id"] != uid or pr["stored_filename"] != sfn or pr["file_size"] != fsize:
            print(f"[DATA VERIFY ERROR] Resume record {rid} mismatch")
            return False

    # 4. ATS Analyses verification
    s_cur.execute("SELECT id, user_id, final_score, ats_readability_score, primary_domain FROM ats_analyses ORDER BY id")
    s_ats = s_cur.fetchall()
    for sa in s_ats:
        aid, uid, fscore, rscore, domain = sa
        p_cur.execute("SELECT id, user_id, final_score, ats_readability_score, primary_domain FROM ats_analyses WHERE id = %s", (aid,))
        pa = p_cur.fetchone()
        if not pa or pa["user_id"] != uid or pa["final_score"] != fscore or pa["primary_domain"] != domain:
            print(f"[DATA VERIFY ERROR] ATS record {aid} mismatch")
            return False

    # 5. Saved & Applied Jobs verification
    s_cur.execute("SELECT id, user_id, job_id, job_title, company FROM saved_jobs ORDER BY id")
    s_saved = s_cur.fetchall()
    for ss in s_saved:
        jid, uid, j_id, title, comp = ss
        p_cur.execute("SELECT id, user_id, job_id, job_title, company FROM saved_jobs WHERE id = %s", (jid,))
        ps = p_cur.fetchone()
        if not ps or ps["user_id"] != uid or ps["job_id"] != j_id or ps["company"] != comp:
            print(f"[DATA VERIFY ERROR] Saved job {jid} mismatch")
            return False

    s_cur.execute("SELECT id, user_id, job_id, job_title, company FROM applied_jobs ORDER BY id")
    s_app = s_cur.fetchall()
    for sa in s_app:
        jid, uid, j_id, title, comp = sa
        p_cur.execute("SELECT id, user_id, job_id, job_title, company FROM applied_jobs WHERE id = %s", (jid,))
        pa = p_cur.fetchone()
        if not pa or pa["user_id"] != uid or pa["job_id"] != j_id or pa["company"] != comp:
            print(f"[DATA VERIFY ERROR] Applied job {jid} mismatch")
            return False

    # 6. Course Progress verification
    s_cur.execute("SELECT id, user_id, course_id, topic_id, completed FROM course_topic_progress ORDER BY id")
    s_cp = s_cur.fetchall()
    for sc in s_cp:
        cid, uid, crs_id, top_id, comp = sc
        p_cur.execute("SELECT id, user_id, course_id, topic_id, completed FROM course_topic_progress WHERE id = %s", (cid,))
        pc = p_cur.fetchone()
        if not pc or pc["user_id"] != uid or pc["course_id"] != crs_id or pc["topic_id"] != top_id or pc["completed"] != comp:
            print(f"[DATA VERIFY ERROR] Course progress {cid} mismatch")
            return False

    return True


def run_migration(table_filter: Optional[str] = None, verify_only: bool = False):
    """Executes full migration or verification."""
    print("=" * 60)
    mode_name = "VERIFICATION ONLY" if verify_only else "SAFE DATA MIGRATION"
    print(f"SKILLBRIDGE.AI — SUPABASE {mode_name}")
    print("=" * 60)

    # 1. Check local source
    if not os.path.exists(DB_PATH):
        print(f"[FATAL] Source database {DB_PATH} not found.")
        sys.exit(1)

    initial_snapshot = get_local_source_snapshot()
    sqlite_conn = sqlite3.connect(DB_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    # 2. Check PostgreSQL target connection
    pg_url = auth_db.get_database_url()
    if not pg_url:
        print("[FATAL] DATABASE_URL or POSTGRES_URL is not set.")
        print("Please configure DATABASE_URL in your environment or .env file before migrating.")
        sys.exit(1)

    try:
        import psycopg2
        import psycopg2.extras
        pg_conn = psycopg2.connect(pg_url, cursor_factory=psycopg2.extras.RealDictCursor)
    except Exception as e:
        print(f"[FATAL] Could not connect to PostgreSQL target: {e}")
        sys.exit(1)

    # 3. Ensure schema initialized in PostgreSQL
    if not verify_only:
        print("[1/4] Ensuring PostgreSQL schema is initialized...")
        # Run init_db under postgres backend
        orig_db_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = pg_url
        try:
            auth_db.init_db()
        finally:
            if orig_db_url:
                os.environ["DATABASE_URL"] = orig_db_url

    # 4. Migrate tables
    target_tables = [table_filter] if table_filter else MIGRATION_TABLES
    table_results = {}
    all_tables_pass = True

    print(f"\n[2/4] {'Verifying' if verify_only else 'Migrating'} Database Tables:")
    for tbl in target_tables:
        if tbl not in TABLE_COLUMNS:
            print(f"  [ERROR] Unknown table: {tbl}")
            continue

        if verify_only:
            s_cur = sqlite_conn.cursor()
            s_cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            s_cnt = s_cur.fetchone()[0]

            p_cur = pg_conn.cursor()
            p_cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            p_res = p_cur.fetchone()
            p_cnt = p_res["count"] if "count" in p_res else list(p_res.values())[0]
            status = "PASS" if s_cnt == p_cnt else "FAIL"
            if status == "FAIL":
                all_tables_pass = False
            table_results[tbl] = (s_cnt, p_cnt, status)
            print(f"  {tbl:25}: {s_cnt:4} -> {p_cnt:4} {status}")
        else:
            s_cnt, p_cnt = migrate_database_table(sqlite_conn, pg_conn, tbl)
            status = "PASS" if s_cnt == p_cnt else "FAIL"
            if status == "FAIL":
                all_tables_pass = False
            table_results[tbl] = (s_cnt, p_cnt, status)
            print(f"  {tbl:25}: {s_cnt:4} -> {p_cnt:4} {status}")

    # 5. Migrate resume storage files
    print(f"\n[3/4] {'Verifying' if verify_only else 'Migrating'} Resume Storage Files:")
    local_files = get_local_resume_files()
    storage_pass = True

    if not verify_only:
        local_cnt, up_cnt, failed = migrate_resume_files_to_supabase(local_files)
        storage_pass = (len(failed) == 0 and up_cnt == local_cnt)
        print(f"  Local files: {local_cnt} | Uploaded: {up_cnt} | Status: {'PASS' if storage_pass else 'FAIL'}")
        if failed:
            print(f"  Failed uploads: {failed}")
    else:
        # Verify objects exist in storage
        verified_count = 0
        for f in local_files:
            if storage_manager.resume_exists(f["user_id"], filename=f["filename"]):
                verified_count += 1
        storage_pass = (verified_count == len(local_files))
        print(f"  Local files: {len(local_files)} | Storage Verified: {verified_count} | Status: {'PASS' if storage_pass else 'FAIL'}")

    # 6. Verify deep data values
    print("\n[4/4] Verifying Relational Data Integrity & Values...")
    values_pass = verify_database_data_values(sqlite_conn, pg_conn)
    print(f"  Deep Data Values Verification: {'PASS' if values_pass else 'FAIL'}")

    # 7. Verify local source immutability
    local_integrity_pass = verify_local_source_integrity(initial_snapshot)
    print(f"  Local Source SQLite Untouched: {'PASS' if local_integrity_pass else 'FAIL'}")
    print(f"  Local Source Uploads Untouched: {'PASS' if local_integrity_pass else 'FAIL'}")

    sqlite_conn.close()
    pg_conn.close()

    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY REPORT")
    print("=" * 60)
    for tbl, (sc, pc, st) in table_results.items():
        print(f"{tbl:25}: {sc} → {pc} {st}")

    print(f"\nMain & ATS Resumes       : {len(local_files)} → {len(local_files)} {'PASS' if storage_pass else 'FAIL'}")
    print(f"Data Values Integrity    : {'PASS' if values_pass else 'FAIL'}")
    print(f"Local SQLite Untouched   : {'PASS' if local_integrity_pass else 'FAIL'}")
    print(f"Local Uploads Untouched  : {'PASS' if local_integrity_pass else 'FAIL'}")
    
    overall_pass = all_tables_pass and storage_pass and values_pass and local_integrity_pass
    print("=" * 60)
    print(f"OVERALL RESULT: {'PASS' if overall_pass else 'FAIL'}")
    print("=" * 60)

    if not overall_pass:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="SkillBridge.AI Supabase Migration Tool")
    parser.add_argument("--dry-run", action="store_true", help="Perform dry run inspection without modifications")
    parser.add_argument("--verify-only", action="store_true", help="Verify parity between SQLite and PostgreSQL/Storage")
    parser.add_argument("--table", type=str, default=None, help="Migrate a specific table only")
    args = parser.parse_args()

    if args.dry_run:
        run_dry_run()
    elif args.verify_only:
        run_migration(table_filter=args.table, verify_only=True)
    else:
        run_migration(table_filter=args.table, verify_only=False)


if __name__ == "__main__":
    main()
