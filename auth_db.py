import os
import re
import sqlite3
import base64
import hashlib
import json
from datetime import datetime, timezone, timedelta
from cryptography.fernet import Fernet

# Default database location inside instance folder
INSTANCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
DEFAULT_DB_PATH = os.path.join(INSTANCE_DIR, "skillbridge.db")


def get_db_path():
    """Returns database path, ensuring instance directory exists."""
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    return os.environ.get("DATABASE_PATH", DEFAULT_DB_PATH)


def get_database_url():
    """
    Returns configured PostgreSQL database URL if present.
    Checks DATABASE_URL, SUPABASE_DB_URL, POSTGRES_URL.
    Normalizes 'postgres://' to 'postgresql://' for standard psycopg2 compatibility.
    """
    raw_url = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL") or os.environ.get("POSTGRES_URL")
    if not raw_url:
        return None
    url = str(raw_url).strip()
    if not url:
        return None
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def is_postgres_backend():
    """
    Returns True if PostgreSQL backend is configured, False otherwise (SQLite).
    """
    return bool(get_database_url())


def _adapt_sql(sql: str, is_postgres: bool) -> str:
    """
    Translates '?' parameter placeholders to '%s' for PostgreSQL
    while preserving strings and literal question marks inside quotes/comments.
    """
    if not is_postgres or not sql:
        return sql

    result = []
    i = 0
    n = len(sql)
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        char = sql[i]
        next_char = sql[i+1] if i + 1 < n else ''

        if in_line_comment:
            result.append(char)
            if char == '\n':
                in_line_comment = False
            i += 1
            continue

        if in_block_comment:
            result.append(char)
            if char == '*' and next_char == '/':
                result.append(next_char)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        if in_single_quote:
            result.append(char)
            if char == "'":
                if next_char == "'":
                    result.append(next_char)
                    i += 2
                    continue
                else:
                    in_single_quote = False
            i += 1
            continue

        if in_double_quote:
            result.append(char)
            if char == '"':
                if next_char == '"':
                    result.append(next_char)
                    i += 2
                    continue
                else:
                    in_double_quote = False
            i += 1
            continue

        if char == '-' and next_char == '-':
            result.append(char)
            result.append(next_char)
            in_line_comment = True
            i += 2
            continue

        if char == '/' and next_char == '*':
            result.append(char)
            result.append(next_char)
            in_block_comment = True
            i += 2
            continue

        if char == "'":
            result.append(char)
            in_single_quote = True
            i += 1
            continue

        if char == '"':
            result.append(char)
            in_double_quote = True
            i += 1
            continue

        if char == '?':
            result.append('%s')
            i += 1
            continue

        result.append(char)
        i += 1

    return ''.join(result)


class SQLiteCursorWrapper:
    """Wrapper around sqlite3.Cursor preserving dict-like row access and standard cursor API."""
    def __init__(self, raw_cursor):
        self._cursor = raw_cursor

    def execute(self, query, params=None):
        if params is not None:
            self._cursor.execute(query, params)
        else:
            self._cursor.execute(query)
        return self

    def executemany(self, query, seq_of_params):
        self._cursor.executemany(query, seq_of_params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    def __iter__(self):
        return iter(self._cursor)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class SQLiteConnectionWrapper:
    """Wrapper around sqlite3.Connection ensuring foreign keys, dict rows, and safe commits."""
    def __init__(self, db_path):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")

    def cursor(self):
        return SQLiteCursorWrapper(self._conn.cursor())

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


class PostgresCursorWrapper:
    """Wrapper around psycopg2 cursor providing ?->%s adaptation, lastrowid tracking, and dict rows."""
    def __init__(self, raw_cursor):
        self._cursor = raw_cursor
        self.lastrowid = None

    def execute(self, query, params=None):
        adapted_query = _adapt_sql(query, is_postgres=True)
        stripped_upper = adapted_query.strip().upper()
        is_insert = stripped_upper.startswith("INSERT INTO")
        has_returning = "RETURNING" in stripped_upper

        if is_insert and not has_returning:
            table_match = re.search(r'INSERT\s+INTO\s+([a-zA-Z0-9_]+)', adapted_query, re.IGNORECASE)
            table_name = table_match.group(1).lower() if table_match else ""
            if table_name and table_name != "user_search_cooldown":
                query_with_ret = adapted_query.rstrip().rstrip(';') + " RETURNING id;"
                try:
                    if params is not None:
                        self._cursor.execute(query_with_ret, params)
                    else:
                        self._cursor.execute(query_with_ret)
                    row = self._cursor.fetchone()
                    if row and "id" in row:
                        self.lastrowid = row["id"]
                    return self
                except Exception:
                    pass

        if params is not None:
            self._cursor.execute(adapted_query, params)
        else:
            self._cursor.execute(adapted_query)
        return self

    def executemany(self, query, seq_of_params):
        adapted_query = _adapt_sql(query, is_postgres=True)
        self._cursor.executemany(adapted_query, seq_of_params)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size=None):
        return self._cursor.fetchmany(size) if size is not None else self._cursor.fetchmany()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

    def __iter__(self):
        return iter(self._cursor)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class PostgresConnectionWrapper:
    """Wrapper around psycopg2 connection providing RealDictCursor, commit/rollback, and safe close."""
    def __init__(self, dsn):
        import psycopg2
        import psycopg2.extras
        self._conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor)

    def cursor(self):
        return PostgresCursorWrapper(self._conn.cursor())

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        if not self._conn.closed:
            self._conn.commit()

    def rollback(self):
        if not self._conn.closed:
            self._conn.rollback()

    def close(self):
        if not self._conn.closed:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_db_connection():
    """
    Returns a database connection wrapper.
    If PostgreSQL is configured (DATABASE_URL), connects to PostgreSQL via psycopg2.
    Otherwise, connects to local SQLite database with foreign keys enabled.
    Both wrappers provide dict-like row access, commit/rollback, and identical cursor semantics.
    """
    if is_postgres_backend():
        db_url = get_database_url()
        return PostgresConnectionWrapper(db_url)
    else:
        db_path = get_db_path()
        return SQLiteConnectionWrapper(db_path)


def _now_iso():
    """Returns current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str):
    """Parses an ISO timestamp string into a timezone-aware datetime."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# --- ENCRYPTION HELPERS FOR SENSITIVE API KEYS ---

def get_encryption_cipher():
    """
    Derives a Fernet encryption cipher from application SECRET_KEY.
    Ensures standard AES-128 CBC/HMAC symmetric encryption.
    """
    secret = os.environ.get(
        "SECRET_KEY", "skillbridge_dev_secret_key_change_in_production_f789a2b"
    ).encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_api_key(raw_key):
    """
    Encrypts a plaintext API key string.
    Returns URL-safe encrypted string or None.
    """
    if not raw_key:
        return None
    cleaned = str(raw_key).strip()
    if not cleaned:
        return None
    return get_encryption_cipher().encrypt(cleaned.encode("utf-8")).decode("utf-8")


def decrypt_api_key(encrypted_key):
    """
    Decrypts an encrypted API key string.
    Returns plaintext string or None if invalid/empty.
    """
    if not encrypted_key:
        return None
    try:
        return get_encryption_cipher().decrypt(encrypted_key.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


def mask_api_key(raw_key):
    """
    Masks an API key for safe display (e.g. ****************abcd).
    """
    if not raw_key:
        return ""
    cleaned = str(raw_key).strip()
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return "*" * (len(cleaned) - 4) + cleaned[-4:]


def mask_api_key_bullet(raw_key):
    """
    Masks an API key with bullet characters for modern UI display (e.g. ••••••••••••6729).
    Never exposes raw key.
    """
    if not raw_key:
        return ""
    cleaned = str(raw_key).strip()
    if len(cleaned) <= 4:
        return "•" * len(cleaned)
    bullet_count = max(8, min(12, len(cleaned) - 4))
    return "•" * bullet_count + cleaned[-4:]


def get_key_fingerprint(raw_key):
    """
    Generates a secure, deterministic one-way SHA-256 fingerprint from a raw API key.
    Never stores or returns raw key.
    """
    if not raw_key:
        return ""
    cleaned = str(raw_key).strip()
    if not cleaned:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]


# --- DATABASE INITIALIZATION & MIGRATIONS ---

def init_db(app=None):
    """
    Initializes database tables if they do not already exist.
    Supports both PostgreSQL (when DATABASE_URL is configured) and SQLite.
    Performs safe in-place migrations to preserve existing data.
    """
    if is_postgres_backend():
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # 1. Users table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

            # 2. Password reset tokens table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS password_reset_tokens (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    used_at TEXT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_reset_token_hash ON password_reset_tokens(token_hash);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_reset_user_id ON password_reset_tokens(user_id);")

            # 3. User API Keys table (encrypted per-user storage)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_api_keys (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    serpapi_key_encrypted TEXT NULL,
                    gemini_api_key_encrypted TEXT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_api_keys_user_id ON user_api_keys(user_id);")

            # 4. User Resumes table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_resumes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    original_filename TEXT NOT NULL,
                    stored_filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    uploaded_at TEXT NOT NULL,
                    processing_status TEXT NOT NULL DEFAULT 'completed',
                    extracted_data_json TEXT NULL,
                    is_current INTEGER NOT NULL DEFAULT 1
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_resumes_user_id ON user_resumes(user_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_resumes_current ON user_resumes(user_id, is_current);")

            # 5. User Persistent Sessions / Remember Tokens table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_sessions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    token_hash TEXT NOT NULL UNIQUE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    user_agent TEXT NULL,
                    ip_address TEXT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_token_hash ON user_sessions(token_hash);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);")

            # 6. Saved Jobs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS saved_jobs (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL,
                    job_title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    company_logo TEXT NULL,
                    location TEXT NULL,
                    employment_type TEXT NULL,
                    experience TEXT NULL,
                    salary TEXT NULL,
                    match_score INTEGER NULL,
                    posted_time TEXT NULL,
                    application_url TEXT NULL,
                    job_description TEXT NULL,
                    source TEXT NULL,
                    openings TEXT NULL,
                    saved_at TEXT NOT NULL,
                    UNIQUE(user_id, job_id)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_jobs_user_id ON saved_jobs(user_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_jobs_user_job ON saved_jobs(user_id, job_id);")

            # 7. Applied Jobs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS applied_jobs (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL,
                    job_title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    company_logo TEXT NULL,
                    location TEXT NULL,
                    employment_type TEXT NULL,
                    experience TEXT NULL,
                    salary TEXT NULL,
                    match_score INTEGER NULL,
                    posted_time TEXT NULL,
                    application_url TEXT NULL,
                    job_description TEXT NULL,
                    source TEXT NULL,
                    openings TEXT NULL,
                    applied_at TEXT NOT NULL,
                    UNIQUE(user_id, job_id)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_applied_jobs_user_id ON applied_jobs(user_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_applied_jobs_user_job ON applied_jobs(user_id, job_id);")

            # 8. Job Search Results table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS job_search_results (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    search_id TEXT NOT NULL UNIQUE,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    skills_json TEXT NOT NULL,
                    roles_json TEXT NOT NULL,
                    role_matches_json TEXT NOT NULL,
                    missing_skills_json TEXT NOT NULL,
                    market_insights_json TEXT NOT NULL,
                    jobs_data_json TEXT NOT NULL,
                    total_jobs INTEGER NOT NULL DEFAULT 0,
                    search_version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_search_user_id ON job_search_results(user_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_search_user_current ON job_search_results(user_id, is_current);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_search_search_id ON job_search_results(search_id);")

            # 9. API Key History table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_key_history (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    service TEXT NOT NULL,
                    key_fingerprint TEXT NOT NULL,
                    masked_key TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    last_used_at TEXT NULL,
                    status TEXT NOT NULL DEFAULT 'Current',
                    last_known_usage INTEGER NULL,
                    last_known_limit INTEGER NULL,
                    last_known_hourly_usage INTEGER NULL,
                    last_known_hourly_limit INTEGER NULL,
                    remaining_searches INTEGER NULL,
                    plan_name TEXT NULL,
                    renewal_date TEXT NULL,
                    last_error_category TEXT NULL,
                    is_current INTEGER NOT NULL DEFAULT 1,
                    details_json TEXT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_hist_user ON api_key_history(user_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_hist_user_svc ON api_key_history(user_id, service);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_hist_user_curr ON api_key_history(user_id, service, is_current);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_hist_fp ON api_key_history(key_fingerprint);")

            # 10. API Usage Logs table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_usage_logs (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    service TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    model TEXT NULL,
                    key_fingerprint TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    success INTEGER NOT NULL DEFAULT 1,
                    error_category TEXT NULL,
                    error_message TEXT NULL,
                    prompt_tokens INTEGER NULL,
                    candidates_tokens INTEGER NULL,
                    total_tokens INTEGER NULL,
                    retry_after_seconds INTEGER NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_user ON api_usage_logs(user_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_user_svc ON api_usage_logs(user_id, service);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_user_time ON api_usage_logs(user_id, timestamp);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_fp ON api_usage_logs(key_fingerprint);")

            # 11. User Search Cooldown table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_search_cooldown (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    last_search_started_at TEXT NULL,
                    cooldown_until TEXT NULL,
                    search_in_progress INTEGER NOT NULL DEFAULT 0,
                    search_started_timestamp DOUBLE PRECISION NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_cooldown_user ON user_search_cooldown(user_id);")

            # 12. ATS Analyses table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ats_analyses (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    filename TEXT NOT NULL,
                    stored_filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL DEFAULT 0,
                    uploaded_at TEXT NOT NULL,
                    analyzed_at TEXT NOT NULL,
                    final_score INTEGER NOT NULL DEFAULT 0,
                    ats_readability_score INTEGER NOT NULL DEFAULT 0,
                    content_quality_score INTEGER NOT NULL DEFAULT 0,
                    skills_score INTEGER NOT NULL DEFAULT 0,
                    experience_projects_score INTEGER NOT NULL DEFAULT 0,
                    completeness_score INTEGER NOT NULL DEFAULT 0,
                    quantification_score INTEGER NOT NULL DEFAULT 0,
                    grammar_consistency_score INTEGER NOT NULL DEFAULT 0,
                    score_message TEXT NULL,
                    score_status TEXT NULL,
                    primary_domain TEXT NULL,
                    parsed_sections_json TEXT NULL,
                    detected_skills_json TEXT NULL,
                    industry_terms_json TEXT NULL,
                    strengths_json TEXT NULL,
                    problems_detected_json TEXT NULL,
                    missing_sections_json TEXT NULL,
                    weak_bullets_json TEXT NULL,
                    recommendations_json TEXT NULL,
                    consistency_findings_json TEXT NULL,
                    analysis_json TEXT NULL,
                    is_current INTEGER NOT NULL DEFAULT 1
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ats_analyses_user_id ON ats_analyses(user_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_ats_analyses_user_curr ON ats_analyses(user_id, is_current);")

            # 13. Course Topic Progress table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS course_topic_progress (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    course_id TEXT NOT NULL,
                    topic_id TEXT NOT NULL,
                    completed INTEGER NOT NULL DEFAULT 0,
                    completed_at TEXT NULL,
                    last_watched_at TEXT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, course_id, topic_id)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_course_progress_user_course ON course_topic_progress(user_id, course_id);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_course_progress_lookup ON course_topic_progress(user_id, course_id, topic_id);")

            conn.commit()
            return

    os.makedirs(INSTANCE_DIR, exist_ok=True)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # 1. Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                email TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
        """)

        # Migration: Ensure last_login_at exists if table was created previously
        cursor.execute("PRAGMA table_info(users);")
        user_columns = [col["name"] for col in cursor.fetchall()]
        if "last_login_at" not in user_columns:
            cursor.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT NULL;")

        # Indexes for fast user lookup
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

        # 2. Password reset tokens table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                used_at TEXT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reset_token_hash ON password_reset_tokens(token_hash);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reset_user_id ON password_reset_tokens(user_id);")

        # 3. User API Keys table (encrypted per-user storage)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                serpapi_key_encrypted TEXT NULL,
                gemini_api_key_encrypted TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_api_keys_user_id ON user_api_keys(user_id);")

        # 4. User Resumes table (per-user resume metadata and extracted analysis)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_resumes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                original_filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                uploaded_at TEXT NOT NULL,
                processing_status TEXT NOT NULL DEFAULT 'completed',
                extracted_data_json TEXT NULL,
                is_current INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_resumes_user_id ON user_resumes(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_resumes_current ON user_resumes(user_id, is_current);")

        # 5. User Persistent Sessions / Remember Tokens table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL,
                user_agent TEXT NULL,
                ip_address TEXT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_token_hash ON user_sessions(token_hash);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);")

        # 6. Saved Jobs table (per-user saved job opportunities)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS saved_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT NOT NULL,
                job_title TEXT NOT NULL,
                company TEXT NOT NULL,
                company_logo TEXT NULL,
                location TEXT NULL,
                employment_type TEXT NULL,
                experience TEXT NULL,
                salary TEXT NULL,
                match_score INTEGER NULL,
                posted_time TEXT NULL,
                application_url TEXT NULL,
                job_description TEXT NULL,
                source TEXT NULL,
                openings TEXT NULL,
                saved_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, job_id)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_jobs_user_id ON saved_jobs(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_saved_jobs_user_job ON saved_jobs(user_id, job_id);")

        # Migration: Ensure openings column exists in saved_jobs
        cursor.execute("PRAGMA table_info(saved_jobs);")
        saved_cols = [col["name"] for col in cursor.fetchall()]
        if "openings" not in saved_cols:
            cursor.execute("ALTER TABLE saved_jobs ADD COLUMN openings TEXT NULL;")

        # 7. Applied Jobs table (per-user confirmed applications)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS applied_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                job_id TEXT NOT NULL,
                job_title TEXT NOT NULL,
                company TEXT NOT NULL,
                company_logo TEXT NULL,
                location TEXT NULL,
                employment_type TEXT NULL,
                experience TEXT NULL,
                salary TEXT NULL,
                match_score INTEGER NULL,
                posted_time TEXT NULL,
                application_url TEXT NULL,
                job_description TEXT NULL,
                source TEXT NULL,
                openings TEXT NULL,
                applied_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, job_id)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_applied_jobs_user_id ON applied_jobs(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_applied_jobs_user_job ON applied_jobs(user_id, job_id);")

        # 8. Job Search Results table (per-user cached/persisted job search results)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS job_search_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                search_id TEXT NOT NULL UNIQUE,
                is_current INTEGER NOT NULL DEFAULT 1,
                skills_json TEXT NOT NULL,
                roles_json TEXT NOT NULL,
                role_matches_json TEXT NOT NULL,
                missing_skills_json TEXT NOT NULL,
                market_insights_json TEXT NOT NULL,
                jobs_data_json TEXT NOT NULL,
                total_jobs INTEGER NOT NULL DEFAULT 0,
                search_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_search_user_id ON job_search_results(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_search_user_current ON job_search_results(user_id, is_current);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_job_search_search_id ON job_search_results(search_id);")

        # 9. API Key History table (tracks lifecycle & metrics of all keys safely)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_key_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                key_fingerprint TEXT NOT NULL,
                masked_key TEXT NOT NULL,
                added_at TEXT NOT NULL,
                last_used_at TEXT NULL,
                status TEXT NOT NULL DEFAULT 'Current',
                last_known_usage INTEGER NULL,
                last_known_limit INTEGER NULL,
                last_known_hourly_usage INTEGER NULL,
                last_known_hourly_limit INTEGER NULL,
                remaining_searches INTEGER NULL,
                plan_name TEXT NULL,
                renewal_date TEXT NULL,
                last_error_category TEXT NULL,
                is_current INTEGER NOT NULL DEFAULT 1,
                details_json TEXT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_hist_user ON api_key_history(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_hist_user_svc ON api_key_history(user_id, service);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_hist_user_curr ON api_key_history(user_id, service, is_current);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_key_hist_fp ON api_key_history(key_fingerprint);")

        # 10. API Usage Logs table (records local events and error classifications)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS api_usage_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service TEXT NOT NULL,
                feature TEXT NOT NULL,
                model TEXT NULL,
                key_fingerprint TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 1,
                error_category TEXT NULL,
                error_message TEXT NULL,
                prompt_tokens INTEGER NULL,
                candidates_tokens INTEGER NULL,
                total_tokens INTEGER NULL,
                retry_after_seconds INTEGER NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_user ON api_usage_logs(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_user_svc ON api_usage_logs(user_id, service);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_user_time ON api_usage_logs(user_id, timestamp);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_fp ON api_usage_logs(key_fingerprint);")

        # 11. User Search Cooldown table (persists search-in-progress and anti-duplicate cooldown)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_search_cooldown (
                user_id INTEGER PRIMARY KEY,
                last_search_started_at TEXT NULL,
                cooldown_until TEXT NULL,
                search_in_progress INTEGER NOT NULL DEFAULT 0,
                search_started_timestamp REAL NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_cooldown_user ON user_search_cooldown(user_id);")

        # 12. ATS Analyses table (persistent per-user authoritative ATS score & structured report)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ats_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                stored_filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                uploaded_at TEXT NOT NULL,
                analyzed_at TEXT NOT NULL,
                final_score INTEGER NOT NULL DEFAULT 0,
                ats_readability_score INTEGER NOT NULL DEFAULT 0,
                content_quality_score INTEGER NOT NULL DEFAULT 0,
                skills_score INTEGER NOT NULL DEFAULT 0,
                experience_projects_score INTEGER NOT NULL DEFAULT 0,
                completeness_score INTEGER NOT NULL DEFAULT 0,
                quantification_score INTEGER NOT NULL DEFAULT 0,
                grammar_consistency_score INTEGER NOT NULL DEFAULT 0,
                score_message TEXT NULL,
                score_status TEXT NULL,
                primary_domain TEXT NULL,
                parsed_sections_json TEXT NULL,
                detected_skills_json TEXT NULL,
                industry_terms_json TEXT NULL,
                strengths_json TEXT NULL,
                problems_detected_json TEXT NULL,
                missing_sections_json TEXT NULL,
                weak_bullets_json TEXT NULL,
                recommendations_json TEXT NULL,
                consistency_findings_json TEXT NULL,
                analysis_json TEXT NULL,
                is_current INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ats_analyses_user_id ON ats_analyses(user_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ats_analyses_user_curr ON ats_analyses(user_id, is_current);")

        # Migration: Ensure quantification_score column exists in ats_analyses
        cursor.execute("PRAGMA table_info(ats_analyses);")
        ats_cols = [col["name"] for col in cursor.fetchall()]
        if ats_cols and "quantification_score" not in ats_cols:
            cursor.execute("ALTER TABLE ats_analyses ADD COLUMN quantification_score INTEGER NOT NULL DEFAULT 0;")

        # 13. Course Topic Progress table (persistent per-user course topic completion and real progress)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS course_topic_progress (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_id TEXT NOT NULL,
                topic_id TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                completed_at TEXT NULL,
                last_watched_at TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, course_id, topic_id)
            );
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_course_progress_user_course ON course_topic_progress(user_id, course_id);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_course_progress_lookup ON course_topic_progress(user_id, course_id, topic_id);")

        conn.commit()



# --- USER AUTHENTICATION CRUD ---

def create_user(username, email, password_hash):
    """
    Creates a new user record.
    Returns the created user id.
    Raises ValueError if username or email already exists.
    """
    norm_username = username.strip()
    norm_email = email.strip().lower()
    now_str = _now_iso()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Check duplicate username
        cursor.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(?)", (norm_username,))
        if cursor.fetchone():
            raise ValueError("USERNAME_EXISTS")

        # Check duplicate email
        cursor.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(?)", (norm_email,))
        if cursor.fetchone():
            raise ValueError("EMAIL_EXISTS")

        cursor.execute("""
            INSERT INTO users (username, email, password_hash, created_at, updated_at, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (norm_username, norm_email, password_hash, now_str, now_str))
        conn.commit()
        return cursor.lastrowid


def get_user_by_login(identifier):
    """
    Finds a user by either username or email address.
    Returns sqlite3.Row dict-like object or None.
    """
    if not identifier:
        return None
    cleaned = identifier.strip().lower()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, username, email, password_hash, created_at, updated_at, last_login_at, is_active
            FROM users
            WHERE LOWER(username) = ? OR LOWER(email) = ?
            LIMIT 1
        """, (cleaned, cleaned))
        return cursor.fetchone()


def get_user_by_id(user_id):
    """
    Retrieves user by primary key id.
    """
    if not user_id:
        return None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, username, email, password_hash, created_at, updated_at, last_login_at, is_active
            FROM users
            WHERE id = ?
            LIMIT 1
        """, (user_id,))
        return cursor.fetchone()


def get_user_by_email(email):
    """
    Retrieves user by normalized email.
    """
    if not email:
        return None
    cleaned = email.strip().lower()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, username, email, password_hash, created_at, updated_at, last_login_at, is_active
            FROM users
            WHERE LOWER(email) = ?
            LIMIT 1
        """, (cleaned,))
        return cursor.fetchone()


def update_last_login(user_id):
    """
    Updates the last_login_at timestamp for a user upon successful authentication.
    """
    if not user_id:
        return
    now_str = _now_iso()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET last_login_at = ?, updated_at = ?
            WHERE id = ?
        """, (now_str, now_str, user_id))
        conn.commit()


def create_password_reset_token(user_id, token_hash, expires_at):
    """
    Stores a password reset token hash for a user.
    Invalidates previous outstanding reset tokens for the user.
    """
    now_str = _now_iso()
    expires_str = expires_at.isoformat() if isinstance(expires_at, datetime) else str(expires_at)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Invalidate existing active tokens for this user
        cursor.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
        """, (now_str, user_id))

        cursor.execute("""
            INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, token_hash, expires_str, now_str))
        conn.commit()
        return cursor.lastrowid


def get_valid_reset_token(token_hash):
    """
    Checks if a token hash is valid, not used, and not expired.
    Returns (token_row, status_string).
    """
    now = datetime.now(timezone.utc)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT t.id AS token_id, t.user_id, t.expires_at, t.used_at,
                   u.id AS u_id, u.username, u.email, u.is_active
            FROM password_reset_tokens t
            JOIN users u ON t.user_id = u.id
            WHERE t.token_hash = ?
            ORDER BY t.created_at DESC
            LIMIT 1
        """, (token_hash,))
        row = cursor.fetchone()

        if not row:
            return None, "NOT_FOUND"

        # Check if already used
        if row["used_at"] is not None:
            return None, "USED"

        # Check if expired
        expires_at = _parse_iso(row["expires_at"])
        if expires_at and expires_at < now:
            return None, "EXPIRED"

        return row, "VALID"


def mark_token_used(token_id):
    """
    Marks a reset token as used.
    """
    now_str = _now_iso()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE id = ?
        """, (now_str, token_id))
        conn.commit()


def update_user_password(user_id, password_hash):
    """
    Updates the password hash and updated_at for a given user.
    Also invalidates all remaining reset tokens for this user.
    Preserves all other user data (API keys, resume, username, email).
    """
    now_str = _now_iso()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE users
            SET password_hash = ?, updated_at = ?
            WHERE id = ?
        """, (password_hash, now_str, user_id))

        cursor.execute("""
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE user_id = ? AND used_at IS NULL
        """, (now_str, user_id))
        conn.commit()


# --- PER-USER API KEYS STORAGE & LIFECYCLE MANAGEMENT ---

def record_api_key_history(
    user_id,
    service,
    raw_key=None,
    key_fingerprint=None,
    masked_key=None,
    status="Current",
    plan_name=None,
    renewal_date=None,
    last_known_usage=None,
    last_known_limit=None,
    last_known_hourly_usage=None,
    last_known_hourly_limit=None,
    remaining_searches=None,
    last_error_category=None,
    details=None,
    is_current=1,
):
    """
    Safely records or updates an API key entry in api_key_history.
    Never stores or logs raw API keys.
    """
    if not user_id or not service:
        return None

    svc = str(service).strip().lower()
    fp = key_fingerprint or (get_key_fingerprint(raw_key) if raw_key else "")
    if not fp:
        return None

    mask = masked_key or (mask_api_key_bullet(raw_key) if raw_key else "")
    now_str = _now_iso()
    details_json = json.dumps(details) if isinstance(details, dict) else (details if isinstance(details, str) else None)

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # If marking as current, archive previous current records for this user and service
        if is_current:
            cursor.execute("""
                UPDATE api_key_history
                SET is_current = 0,
                    status = CASE WHEN status IN ('Current', 'ACTIVE', 'Active') THEN 'Replaced' ELSE status END
                WHERE user_id = ? AND service = ? AND is_current = 1 AND key_fingerprint != ?
            """, (user_id, svc, fp))

        # Check if record exists for this exact user, service and fingerprint
        cursor.execute("""
            SELECT id, status, plan_name, renewal_date, last_known_usage, last_known_limit,
                   last_known_hourly_usage, last_known_hourly_limit, remaining_searches, details_json
            FROM api_key_history
            WHERE user_id = ? AND service = ? AND key_fingerprint = ?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id, svc, fp))
        existing = cursor.fetchone()

        if existing:
            # Update existing record
            upd_status = status if status is not None else existing["status"]
            upd_plan = plan_name if plan_name is not None else existing["plan_name"]
            upd_renewal = renewal_date if renewal_date is not None else existing["renewal_date"]
            upd_usage = last_known_usage if last_known_usage is not None else existing["last_known_usage"]
            upd_limit = last_known_limit if last_known_limit is not None else existing["last_known_limit"]
            upd_h_usage = last_known_hourly_usage if last_known_hourly_usage is not None else existing["last_known_hourly_usage"]
            upd_h_limit = last_known_hourly_limit if last_known_hourly_limit is not None else existing["last_known_hourly_limit"]
            upd_rem = remaining_searches if remaining_searches is not None else existing["remaining_searches"]
            upd_details = details_json if details_json is not None else existing["details_json"]

            cursor.execute("""
                UPDATE api_key_history
                SET masked_key = COALESCE(?, masked_key),
                    status = ?,
                    plan_name = ?,
                    renewal_date = ?,
                    last_known_usage = ?,
                    last_known_limit = ?,
                    last_known_hourly_usage = ?,
                    last_known_hourly_limit = ?,
                    remaining_searches = ?,
                    last_error_category = COALESCE(?, last_error_category),
                    is_current = ?,
                    details_json = ?
                WHERE id = ?
            """, (
                mask or None,
                upd_status,
                upd_plan,
                upd_renewal,
                upd_usage,
                upd_limit,
                upd_h_usage,
                upd_h_limit,
                upd_rem,
                last_error_category,
                1 if is_current else 0,
                upd_details,
                existing["id"],
            ))
            conn.commit()
            return existing["id"]
        else:
            # Insert new record
            cursor.execute("""
                INSERT INTO api_key_history (
                    user_id, service, key_fingerprint, masked_key, added_at,
                    status, plan_name, renewal_date, last_known_usage, last_known_limit,
                    last_known_hourly_usage, last_known_hourly_limit, remaining_searches,
                    last_error_category, is_current, details_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id,
                svc,
                fp,
                mask,
                now_str,
                status,
                plan_name,
                renewal_date,
                last_known_usage,
                last_known_limit,
                last_known_hourly_usage,
                last_known_hourly_limit,
                remaining_searches,
                last_error_category,
                1 if is_current else 0,
                details_json,
            ))
            conn.commit()
            return cursor.lastrowid


def update_key_history_status(
    user_id,
    service,
    key_fingerprint,
    status=None,
    plan_name=None,
    renewal_date=None,
    last_known_usage=None,
    last_known_limit=None,
    last_known_hourly_usage=None,
    last_known_hourly_limit=None,
    remaining_searches=None,
    last_error_category=None,
    last_used=False,
    details=None,
):
    """
    Updates the status and metadata for a specific key fingerprint in api_key_history.
    """
    if not user_id or not service or not key_fingerprint:
        return False

    svc = str(service).strip().lower()
    now_str = _now_iso()
    details_json = json.dumps(details) if isinstance(details, dict) else (details if isinstance(details, str) else None)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, status, plan_name, renewal_date, last_known_usage, last_known_limit,
                   last_known_hourly_usage, last_known_hourly_limit, remaining_searches, details_json
            FROM api_key_history
            WHERE user_id = ? AND service = ? AND key_fingerprint = ?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id, svc, key_fingerprint))
        row = cursor.fetchone()
        if not row:
            return False

        upd_status = status if status is not None else row["status"]
        upd_plan = plan_name if plan_name is not None else row["plan_name"]
        upd_renewal = renewal_date if renewal_date is not None else row["renewal_date"]
        upd_usage = last_known_usage if last_known_usage is not None else row["last_known_usage"]
        upd_limit = last_known_limit if last_known_limit is not None else row["last_known_limit"]
        upd_h_usage = last_known_hourly_usage if last_known_hourly_usage is not None else row["last_known_hourly_usage"]
        upd_h_limit = last_known_hourly_limit if last_known_hourly_limit is not None else row["last_known_hourly_limit"]
        upd_rem = remaining_searches if remaining_searches is not None else row["remaining_searches"]
        upd_details = details_json if details_json is not None else row["details_json"]

        last_used_sql = ", last_used_at = ?" if last_used else ""
        params = [
            upd_status,
            upd_plan,
            upd_renewal,
            upd_usage,
            upd_limit,
            upd_h_usage,
            upd_h_limit,
            upd_rem,
            last_error_category,
            upd_details,
        ]
        if last_used:
            params.append(now_str)
        params.append(row["id"])

        cursor.execute(f"""
            UPDATE api_key_history
            SET status = ?,
                plan_name = ?,
                renewal_date = ?,
                last_known_usage = ?,
                last_known_limit = ?,
                last_known_hourly_usage = ?,
                last_known_hourly_limit = ?,
                remaining_searches = ?,
                last_error_category = COALESCE(?, last_error_category),
                details_json = ?
                {last_used_sql}
            WHERE id = ?
        """, tuple(params))
        conn.commit()
        return True


def get_api_key_history(user_id, service=None):
    """
    Retrieves API key history for an authenticated user.
    Never returns raw keys.
    Returns list of dicts with masked keys and safe metrics.
    """
    if not user_id:
        return []

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if service:
            svc = str(service).strip().lower()
            cursor.execute("""
                SELECT id, user_id, service, key_fingerprint, masked_key, added_at,
                       last_used_at, status, last_known_usage, last_known_limit,
                       last_known_hourly_usage, last_known_hourly_limit, remaining_searches,
                       plan_name, renewal_date, last_error_category, is_current, details_json
                FROM api_key_history
                WHERE user_id = ? AND service = ?
                ORDER BY added_at DESC, id DESC
            """, (user_id, svc))
        else:
            cursor.execute("""
                SELECT id, user_id, service, key_fingerprint, masked_key, added_at,
                       last_used_at, status, last_known_usage, last_known_limit,
                       last_known_hourly_usage, last_known_hourly_limit, remaining_searches,
                       plan_name, renewal_date, last_error_category, is_current, details_json
                FROM api_key_history
                WHERE user_id = ?
                ORDER BY added_at DESC, id DESC
            """, (user_id,))
        rows = cursor.fetchall()

        results = []
        for r in rows:
            details = None
            if r["details_json"]:
                try:
                    details = json.loads(r["details_json"])
                except Exception:
                    details = None
            results.append({
                "id": r["id"],
                "user_id": r["user_id"],
                "service": r["service"],
                "service_display": "SerpAPI" if "serp" in r["service"].lower() else "Gemini",
                "key_fingerprint": r["key_fingerprint"],
                "masked_key": r["masked_key"],
                "added_at": r["added_at"],
                "last_used_at": r["last_used_at"],
                "status": r["status"],
                "last_known_usage": r["last_known_usage"],
                "last_known_limit": r["last_known_limit"],
                "last_known_hourly_usage": r["last_known_hourly_usage"],
                "last_known_hourly_limit": r["last_known_hourly_limit"],
                "remaining_searches": r["remaining_searches"],
                "plan_name": r["plan_name"],
                "renewal_date": r["renewal_date"],
                "last_error_category": r["last_error_category"],
                "is_current": bool(r["is_current"]),
                "details": details,
            })
        return results


def save_user_api_keys(user_id, serpapi_key=None, gemini_api_key=None):
    """
    Saves or updates encrypted API keys for a specific user.
    If a key parameter is None or empty string, the existing key is preserved.
    Automatically archives replaced keys in api_key_history and creates fresh tracking for new keys.
    """
    if not user_id:
        return False
    now_str = _now_iso()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, serpapi_key_encrypted, gemini_api_key_encrypted
            FROM user_api_keys
            WHERE user_id = ?
        """, (user_id,))
        existing = cursor.fetchone()

        old_serp_plain = decrypt_api_key(existing["serpapi_key_encrypted"]) if existing else None
        old_gem_plain = decrypt_api_key(existing["gemini_api_key_encrypted"]) if existing else None

        new_serp_provided = serpapi_key is not None and bool(str(serpapi_key).strip())
        new_gem_provided = gemini_api_key is not None and bool(str(gemini_api_key).strip())

        clean_serp = str(serpapi_key).strip() if new_serp_provided else None
        clean_gem = str(gemini_api_key).strip() if new_gem_provided else None

        if existing:
            new_serp_enc = encrypt_api_key(clean_serp) if new_serp_provided else existing["serpapi_key_encrypted"]
            new_gem_enc = encrypt_api_key(clean_gem) if new_gem_provided else existing["gemini_api_key_encrypted"]

            cursor.execute("""
                UPDATE user_api_keys
                SET serpapi_key_encrypted = ?, gemini_api_key_encrypted = ?, updated_at = ?
                WHERE user_id = ?
            """, (new_serp_enc, new_gem_enc, now_str, user_id))
        else:
            new_serp_enc = encrypt_api_key(clean_serp) if new_serp_provided else None
            new_gem_enc = encrypt_api_key(clean_gem) if new_gem_provided else None

            cursor.execute("""
                INSERT INTO user_api_keys (user_id, serpapi_key_encrypted, gemini_api_key_encrypted, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, new_serp_enc, new_gem_enc, now_str, now_str))

        conn.commit()

    # Manage key history records
    if new_serp_provided and clean_serp != old_serp_plain:
        record_api_key_history(
            user_id=user_id,
            service="serpapi",
            raw_key=clean_serp,
            status="Current",
            is_current=1,
        )
    elif old_serp_plain and not existing:
        # First time recording existing
        record_api_key_history(
            user_id=user_id,
            service="serpapi",
            raw_key=old_serp_plain,
            status="Current",
            is_current=1,
        )

    if new_gem_provided and clean_gem != old_gem_plain:
        record_api_key_history(
            user_id=user_id,
            service="gemini",
            raw_key=clean_gem,
            status="Current",
            is_current=1,
        )
    elif old_gem_plain and not existing:
        record_api_key_history(
            user_id=user_id,
            service="gemini",
            raw_key=old_gem_plain,
            status="Current",
            is_current=1,
        )

    return True


def get_user_api_keys(user_id, decrypted=True):
    """
    Retrieves the API keys belonging to a specific authenticated user.
    Returns a dictionary with decrypted or encrypted values, safe masked strings, and fingerprints.
    """
    if not user_id:
        return {
            "serpapi_key": None,
            "gemini_api_key": None,
            "serpapi_masked": "",
            "gemini_masked": "",
            "serpapi_masked_bullet": "",
            "gemini_masked_bullet": "",
            "serpapi_fingerprint": "",
            "gemini_fingerprint": "",
            "has_serpapi": False,
            "has_gemini": False,
        }

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, serpapi_key_encrypted, gemini_api_key_encrypted, created_at, updated_at
            FROM user_api_keys
            WHERE user_id = ?
            LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()

        if not row:
            return {
                "serpapi_key": None,
                "gemini_api_key": None,
                "serpapi_masked": "",
                "gemini_masked": "",
                "serpapi_masked_bullet": "",
                "gemini_masked_bullet": "",
                "serpapi_fingerprint": "",
                "gemini_fingerprint": "",
                "has_serpapi": False,
                "has_gemini": False,
            }

        serp_enc = row["serpapi_key_encrypted"]
        gem_enc = row["gemini_api_key_encrypted"]

        if decrypted:
            serp_val = decrypt_api_key(serp_enc)
            gem_val = decrypt_api_key(gem_enc)
        else:
            serp_val = serp_enc
            gem_val = gem_enc

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "serpapi_key": serp_val,
            "gemini_api_key": gem_val,
            "serpapi_masked": mask_api_key(serp_val) if serp_val else "",
            "gemini_masked": mask_api_key(gem_val) if gem_val else "",
            "serpapi_masked_bullet": mask_api_key_bullet(serp_val) if serp_val else "",
            "gemini_masked_bullet": mask_api_key_bullet(gem_val) if gem_val else "",
            "serpapi_fingerprint": get_key_fingerprint(serp_val) if serp_val else "",
            "gemini_fingerprint": get_key_fingerprint(gem_val) if gem_val else "",
            "has_serpapi": bool(serp_val),
            "has_gemini": bool(gem_val),
            "updated_at": row["updated_at"],
        }


def delete_user_api_key(user_id, service_name):
    """
    Clears a specific API key ('serpapi' or 'gemini') for a user.
    Marks key in api_key_history as is_current = 0 and status = 'Removed'.
    """
    if not user_id or not service_name:
        return False
    now_str = _now_iso()
    svc = str(service_name).strip().lower()
    col = "serpapi_key_encrypted" if "serp" in svc else "gemini_api_key_encrypted"
    hist_svc = "serpapi" if "serp" in svc else "gemini"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            UPDATE user_api_keys
            SET {col} = NULL, updated_at = ?
            WHERE user_id = ?
        """, (now_str, user_id))

        cursor.execute("""
            UPDATE api_key_history
            SET is_current = 0, status = 'Removed'
            WHERE user_id = ? AND service = ? AND is_current = 1
        """, (user_id, hist_svc))

        conn.commit()
        return True


def log_api_usage(
    user_id,
    service,
    feature,
    key_fingerprint,
    success=True,
    error_category=None,
    error_message=None,
    model=None,
    prompt_tokens=None,
    candidates_tokens=None,
    total_tokens=None,
    retry_after_seconds=None,
):
    """
    Safely logs an API usage event without logging raw keys, passwords, or secret headers.
    """
    if not user_id or not service:
        return None
    svc = str(service).strip().lower()
    fp = key_fingerprint or ""
    now_str = _now_iso()
    sanitized_msg = str(error_message)[:500] if error_message else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO api_usage_logs (
                user_id, service, feature, model, key_fingerprint, timestamp,
                success, error_category, error_message, prompt_tokens,
                candidates_tokens, total_tokens, retry_after_seconds
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            svc,
            feature or "general",
            model,
            fp,
            now_str,
            1 if success else 0,
            error_category,
            sanitized_msg,
            prompt_tokens,
            candidates_tokens,
            total_tokens,
            retry_after_seconds,
        ))
        conn.commit()
        return cursor.lastrowid


def get_gemini_usage_summary(user_id, key_fingerprint=None, configured_model=None):
    """
    Calculates current local Gemini usage metrics for the authenticated user:
    - requests this minute
    - requests today
    - last request timestamp
    - last error category / status
    Does NOT invent fake provider quotas.
    """
    if not user_id:
        return {
            "model": configured_model or "gemini-2.5-flash",
            "requests_this_minute": 0,
            "requests_today": 0,
            "last_request": None,
            "status": "ACTIVE",
            "known_limits": "Provider-managed limits — check Google AI Studio",
            "retry_after": None,
        }

    now_utc = datetime.now(timezone.utc)
    one_minute_ago = (now_utc - timedelta(seconds=60)).isoformat()
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Requests in the last 60 seconds
        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM api_usage_logs
            WHERE user_id = ? AND service = 'gemini' AND timestamp >= ?
        """, (user_id, one_minute_ago))
        row_min = cursor.fetchone()
        reqs_min = row_min["c"] if row_min else 0

        # Requests today
        cursor.execute("""
            SELECT COUNT(*) AS c
            FROM api_usage_logs
            WHERE user_id = ? AND service = 'gemini' AND timestamp >= ?
        """, (user_id, today_start_utc))
        row_day = cursor.fetchone()
        reqs_today = row_day["c"] if row_day else 0

        # Last request details
        cursor.execute("""
            SELECT timestamp, success, error_category, model, retry_after_seconds
            FROM api_usage_logs
            WHERE user_id = ? AND service = 'gemini'
            ORDER BY timestamp DESC, id DESC
            LIMIT 1
        """, (user_id,))
        last_req = cursor.fetchone()

        status = "ACTIVE"
        last_time = None
        retry_after = None
        used_model = configured_model or "gemini-2.5-flash"

        if last_req:
            last_time = last_req["timestamp"]
            if last_req["model"]:
                used_model = last_req["model"]
            if not last_req["success"]:
                err_cat = last_req["error_category"]
                if err_cat in ("RATE_LIMIT", "QUOTA_EXHAUSTED"):
                    status = "RATE LIMITED"
                elif err_cat == "MODEL_UNAVAILABLE":
                    status = "MODEL UNAVAILABLE"
                elif err_cat in ("INVALID_KEY", "PERMISSION_DENIED"):
                    status = "INVALID KEY"
                else:
                    status = "UNAVAILABLE"
            if last_req["retry_after_seconds"]:
                retry_after = last_req["retry_after_seconds"]

        return {
            "model": used_model,
            "requests_this_minute": reqs_min,
            "requests_today": reqs_today,
            "last_request": last_time,
            "status": status,
            "known_limits": "Provider-managed limits — check Google AI Studio",
            "retry_after": retry_after,
        }


def get_user_search_cooldown(user_id, cooldown_seconds=60):
    """
    Checks user search-in-progress and 60-second application cooldown.
    Returns: {
        "in_progress": bool,
        "is_cooldown": bool,
        "remaining_seconds": int,
        "cooldown_until": str|None
    }
    """
    if not user_id:
        return {"in_progress": False, "is_cooldown": False, "remaining_seconds": 0, "cooldown_until": None}

    now_utc = datetime.now(timezone.utc)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT user_id, last_search_started_at, cooldown_until, search_in_progress, search_started_timestamp
            FROM user_search_cooldown
            WHERE user_id = ?
        """, (user_id,))
        row = cursor.fetchone()

        if not row:
            return {"in_progress": False, "is_cooldown": False, "remaining_seconds": 0, "cooldown_until": None}

        in_progress = bool(row["search_in_progress"])
        started_ts = row["search_started_timestamp"]
        # Safety timeout: if search in progress is older than 120 seconds, auto-clear
        if in_progress and started_ts and (now_utc.timestamp() - started_ts > 120):
            cursor.execute("""
                UPDATE user_search_cooldown
                SET search_in_progress = 0
                WHERE user_id = ?
            """, (user_id,))
            conn.commit()
            in_progress = False

        cooldown_until_str = row["cooldown_until"]
        is_cooldown = False
        remaining_sec = 0

        if cooldown_until_str:
            cd_dt = _parse_iso(cooldown_until_str)
            if cd_dt and cd_dt > now_utc:
                diff = (cd_dt - now_utc).total_seconds()
                if diff > 0:
                    is_cooldown = True
                    remaining_sec = int(round(diff))

        return {
            "in_progress": in_progress,
            "is_cooldown": is_cooldown,
            "remaining_seconds": remaining_sec,
            "cooldown_until": cooldown_until_str if is_cooldown else None,
        }


def set_search_in_progress(user_id, in_progress=True):
    """
    Sets search_in_progress flag for user to prevent simultaneous duplicate searches.
    """
    if not user_id:
        return False
    now_utc = datetime.now(timezone.utc)
    now_str = now_utc.isoformat()
    started_ts = now_utc.timestamp() if in_progress else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_search_cooldown (user_id, search_in_progress, search_started_timestamp, last_search_started_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                search_in_progress = excluded.search_in_progress,
                search_started_timestamp = excluded.search_started_timestamp,
                last_search_started_at = CASE WHEN excluded.search_in_progress = 1 THEN excluded.last_search_started_at ELSE user_search_cooldown.last_search_started_at END
        """, (user_id, 1 if in_progress else 0, started_ts, now_str))
        conn.commit()
        return True


def set_user_search_cooldown(user_id, cooldown_seconds=60):
    """
    Activates the 60-second application cooldown for a user after a SUCCESSFUL search.
    Clears search_in_progress.
    """
    if not user_id:
        return False
    now_utc = datetime.now(timezone.utc)
    cooldown_until = (now_utc + timedelta(seconds=cooldown_seconds)).isoformat()
    now_str = now_utc.isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_search_cooldown (user_id, last_search_started_at, cooldown_until, search_in_progress, search_started_timestamp)
            VALUES (?, ?, ?, 0, NULL)
            ON CONFLICT(user_id) DO UPDATE SET
                last_search_started_at = excluded.last_search_started_at,
                cooldown_until = excluded.cooldown_until,
                search_in_progress = 0,
                search_started_timestamp = NULL
        """, (user_id, now_str, cooldown_until))
        conn.commit()
        return True


def clear_user_search_cooldown(user_id, clear_cooldown=False):
    """
    Clears search_in_progress state (e.g. after a failed search attempt).
    If clear_cooldown is True, also clears cooldown_until.
    """
    if not user_id:
        return False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if clear_cooldown:
            cursor.execute("""
                UPDATE user_search_cooldown
                SET search_in_progress = 0, search_started_timestamp = NULL, cooldown_until = NULL
                WHERE user_id = ?
            """, (user_id,))
        else:
            cursor.execute("""
                UPDATE user_search_cooldown
                SET search_in_progress = 0, search_started_timestamp = NULL
                WHERE user_id = ?
            """, (user_id,))
        conn.commit()
        return True


# --- CANONICAL PER-USER RESUME STORAGE HELPERS ---

def get_user_upload_dir(user_id, base_dir=None):
    """
    Returns the isolated canonical upload directory for a specific user.
    Example: uploads/user_123/
    """
    if not base_dir:
        try:
            from flask import current_app
            base_dir = current_app.config.get("UPLOAD_FOLDER", "uploads")
        except Exception:
            base_dir = os.environ.get("UPLOAD_FOLDER", "uploads")
    return os.path.join(base_dir, f"user_{user_id}")


def get_canonical_main_resume_path(user_id, base_dir=None):
    """
    Returns the canonical persistent path for User's Main Profile Resume:
    uploads/user_<user_id>/main_resume.pdf
    """
    return os.path.join(get_user_upload_dir(user_id, base_dir), "main_resume.pdf")


def get_canonical_ats_resume_path(user_id, ext=".pdf", extension=None, base_dir=None):
    """
    Returns the canonical persistent path for User's Standalone ATS Analysis Resume:
    uploads/user_<user_id>/ats_resume<ext> (e.g. ats_resume.pdf or ats_resume.docx)
    """
    if extension is not None:
        ext = extension
    if not ext.startswith("."):
        ext = f".{ext}"
    return os.path.join(get_user_upload_dir(user_id, base_dir), f"ats_resume{ext}")


def save_user_resume(
    user_id,
    original_filename,
    stored_filename,
    file_path,
    file_size=0,
    file_type="pdf",
    extracted_data=None,
    processing_status="completed",
):
    """
    Saves a Main Profile Resume record permanently linked to the user's ID.
    Maintains only ONE active current Main Profile Resume per user.
    Safely removes old physical main resume files if different from the new canonical path.
    NEVER removes or interferes with the ATS Resume file.
    """
    if not user_id:
        return None

    now_str = _now_iso()
    extracted_json = json.dumps(extracted_data) if extracted_data is not None else None

    old_file_path = None
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Find previous active main resume file path to clean up safely
        cursor.execute("""
            SELECT file_path FROM user_resumes
            WHERE user_id = ? AND is_current = 1
        """, (user_id,))
        old_rows = cursor.fetchall()
        if old_rows:
            old_file_path = old_rows[0]["file_path"]

        # Mark previous resumes as not current
        cursor.execute("UPDATE user_resumes SET is_current = 0 WHERE user_id = ?", (user_id,))

        cursor.execute("""
            INSERT INTO user_resumes (
                user_id, original_filename, stored_filename, file_path,
                file_type, file_size, uploaded_at, processing_status,
                extracted_data_json, is_current
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            user_id,
            original_filename,
            stored_filename,
            file_path,
            file_type,
            file_size,
            now_str,
            processing_status,
            extracted_json,
        ))
        conn.commit()
        last_id = cursor.lastrowid

    # Clean up old physical file if it exists and is different from new canonical file
    if old_file_path and old_file_path != file_path and os.path.exists(old_file_path):
        # Safety guard: never delete if it's the ats_resume file
        if not os.path.basename(old_file_path).startswith("ats_resume"):
            try:
                os.remove(old_file_path)
            except Exception as e:
                print(f"[Main Resume Cleanup] Notice removing old file {old_file_path}: {e}")

    return last_id


def get_user_resume(user_id, current_only=True):
    """
    Retrieves the stored resume data for a specific authenticated user.
    """
    if not user_id:
        return None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        if current_only:
            cursor.execute("""
                SELECT id, user_id, original_filename, stored_filename, file_path,
                       file_type, file_size, uploaded_at, processing_status,
                       extracted_data_json, is_current
                FROM user_resumes
                WHERE user_id = ? AND is_current = 1
                ORDER BY uploaded_at DESC
                LIMIT 1
            """, (user_id,))
        else:
            cursor.execute("""
                SELECT id, user_id, original_filename, stored_filename, file_path,
                       file_type, file_size, uploaded_at, processing_status,
                       extracted_data_json, is_current
                FROM user_resumes
                WHERE user_id = ?
                ORDER BY uploaded_at DESC
                LIMIT 1
            """, (user_id,))
        row = cursor.fetchone()

        if not row:
            return None

        extracted = None
        if row["extracted_data_json"]:
            try:
                extracted = json.loads(row["extracted_data_json"])
            except Exception:
                extracted = None

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "original_filename": row["original_filename"],
            "stored_filename": row["stored_filename"],
            "file_path": row["file_path"],
            "file_type": row["file_type"],
            "file_size": row["file_size"],
            "uploaded_at": row["uploaded_at"],
            "processing_status": row["processing_status"],
            "extracted_data": extracted,
            "is_current": bool(row["is_current"]),
        }


def get_all_user_resumes(user_id):
    """
    Retrieves all resume history for a user, ordered from newest to oldest.
    """
    if not user_id:
        return []

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, original_filename, stored_filename, file_path,
                   file_type, file_size, uploaded_at, processing_status,
                   extracted_data_json, is_current
            FROM user_resumes
            WHERE user_id = ?
            ORDER BY uploaded_at DESC
        """, (user_id,))
        rows = cursor.fetchall()

        results = []
        for row in rows:
            extracted = None
            if row["extracted_data_json"]:
                try:
                    extracted = json.loads(row["extracted_data_json"])
                except Exception:
                    extracted = None
            results.append({
                "id": row["id"],
                "user_id": row["user_id"],
                "original_filename": row["original_filename"],
                "stored_filename": row["stored_filename"],
                "file_path": row["file_path"],
                "file_type": row["file_type"],
                "file_size": row["file_size"],
                "uploaded_at": row["uploaded_at"],
                "processing_status": row["processing_status"],
                "extracted_data": extracted,
                "is_current": bool(row["is_current"]),
            })
        return results


# --- PERSISTENT USER SESSIONS / REMEMBER TOKENS CRUD ---

def create_remember_token(user_id, token_hash, expires_at, user_agent=None, ip_address=None):
    """
    Stores a hashed remember token for persistent session management.
    """
    if not user_id or not token_hash:
        return None
    now_str = _now_iso()
    expires_str = expires_at.isoformat() if isinstance(expires_at, datetime) else str(expires_at)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO user_sessions (user_id, token_hash, expires_at, created_at, last_used_at, user_agent, ip_address)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, token_hash, expires_str, now_str, now_str, user_agent, ip_address))
        conn.commit()
        return cursor.lastrowid


def get_user_by_remember_token(token_hash):
    """
    Validates a hashed remember token and retrieves the associated active user.
    Updates last_used_at timestamp if valid.
    Returns (user_row, session_row) or (None, None).
    """
    if not token_hash:
        return None, None

    now = datetime.now(timezone.utc)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.id AS session_id, s.user_id, s.expires_at, s.created_at, s.last_used_at,
                   u.id AS u_id, u.username, u.email, u.password_hash, u.created_at AS user_created_at,
                   u.updated_at AS user_updated_at, u.last_login_at, u.is_active
            FROM user_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.token_hash = ?
            LIMIT 1
        """, (token_hash,))
        row = cursor.fetchone()

        if not row:
            return None, None

        if not row["is_active"]:
            return None, None

        expires_at = _parse_iso(row["expires_at"])
        if expires_at and expires_at < now:
            # Token is expired, delete it
            cursor.execute("DELETE FROM user_sessions WHERE id = ?", (row["session_id"],))
            conn.commit()
            return None, None

        # Update last_used_at
        now_str = _now_iso()
        cursor.execute("UPDATE user_sessions SET last_used_at = ? WHERE id = ?", (now_str, row["session_id"]))
        conn.commit()

        user_dict = {
            "id": row["u_id"],
            "username": row["username"],
            "email": row["email"],
            "password_hash": row["password_hash"],
            "created_at": row["user_created_at"],
            "updated_at": row["user_updated_at"],
            "last_login_at": row["last_login_at"],
            "is_active": bool(row["is_active"]),
        }
        session_dict = {
            "id": row["session_id"],
            "user_id": row["user_id"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "last_used_at": now_str,
        }
        return user_dict, session_dict


def delete_remember_token(token_hash):
    """
    Deletes a specific remember token (e.g. on logout).
    """
    if not token_hash:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_sessions WHERE token_hash = ?", (token_hash,))
        conn.commit()


def delete_all_user_remember_tokens(user_id):
    """
    Revokes all active remember tokens for a specific user (e.g. password change / full logout).
    """
    if not user_id:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        conn.commit()


# --- DETERMINISTIC JOB IDENTIFIER GENERATION ---

def generate_job_id(company, title, location="", application_url=""):
    """
    Generates a deterministic, stable, collision-resistant identifier for a job.
    Uses normalized fields: company + title + location + application_url.
    Ensures identical jobs produce the same job_id across all searches and pages.
    """
    norm_comp = (company or "").strip().lower()
    norm_title = (title or "").strip().lower()
    norm_loc = (location or "").strip().lower()
    norm_url = (application_url or "").strip()
    raw = f"{norm_comp}|{norm_title}|{norm_loc}|{norm_url}"
    return f"job_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _normalize_job_payload(job_data):
    """
    Normalizes a job dictionary or incoming payload into consistent database fields.
    """
    if not isinstance(job_data, dict):
        job_data = {}

    title = (job_data.get("title") or job_data.get("job_title") or "Software Engineer").strip()
    company = (job_data.get("company_name") or job_data.get("company") or "Tech Company").strip()
    location = (job_data.get("location") or "India").strip()
    
    # Check application URL candidates
    app_url = job_data.get("apply_link") or job_data.get("application_url") or job_data.get("link")
    if not app_url and job_data.get("apply_options") and isinstance(job_data["apply_options"], list) and len(job_data["apply_options"]) > 0:
        app_url = job_data["apply_options"][0].get("link")
    if not app_url:
        app_url = job_data.get("share_link") or ""

    # Stable Job ID
    job_id = job_data.get("job_id")
    if not job_id:
        job_id = generate_job_id(company, title, location, app_url)

    # Company Logo / Brand
    company_logo = (
        job_data.get("company_brand")
        or job_data.get("thumbnail")
        or job_data.get("company_logo")
        or "generic"
    )

    # Employment Type / Schedule
    employment_type = job_data.get("job_type") or job_data.get("employment_type") or "Full-time"

    # Experience (Empty if not mentioned)
    exp_val = job_data.get("experience")
    experience = str(exp_val).strip() if exp_val is not None else ""

    # Salary (Not mentioned if missing)
    sal_val = job_data.get("salary")
    salary = str(sal_val).strip() if sal_val and str(sal_val).strip().lower() not in ["none", "null", ""] else "Not mentioned"

    # Match score (Preserve exact numeric score)
    match_score_raw = job_data.get("match_percent") if job_data.get("match_percent") is not None else job_data.get("match_score")
    try:
        match_score = int(float(str(match_score_raw).replace("%", "").strip())) if match_score_raw is not None else 0
    except Exception:
        match_score = 0

    # Posted Time (Real time from source)
    posted_time = job_data.get("posted_at") or job_data.get("posted_time") or ""

    # Openings / Vacancy Count (Strict positive integer string or None for DB storage)
    from job_matcher import _clean_and_validate_openings_count, extract_openings_info
    openings_raw = job_data.get("openings")
    openings_val = _clean_and_validate_openings_count(openings_raw)
    if not openings_val:
        # Check if job_data has openings in description or other fields
        extracted = extract_openings_info(job_data)
        if extracted != "NA":
            openings_val = extracted
        else:
            openings_val = None

    # Job Description / Source
    job_description = job_data.get("description") or job_data.get("job_description") or ""
    source = job_data.get("source") or "Google Jobs / SerpAPI"

    return {
        "job_id": job_id,
        "job_title": title,
        "company": company,
        "company_logo": company_logo,
        "location": location,
        "employment_type": employment_type,
        "experience": experience,
        "salary": salary,
        "match_score": match_score,
        "posted_time": posted_time,
        "application_url": app_url,
        "job_description": job_description,
        "source": source,
        "openings": openings_val,
    }


def _row_to_job_dict(row):
    """
    Converts a database Row from saved_jobs or applied_jobs into a standardized job dictionary
    ready for template rendering.
    """
    if not row:
        return None
    return {
        "job_id": row["job_id"],
        "title": row["job_title"],
        "job_title": row["job_title"],
        "company_name": row["company"],
        "company": row["company"],
        "company_brand": row["company_logo"] or "generic",
        "thumbnail": row["company_logo"] if (row["company_logo"] and str(row["company_logo"]).startswith("http")) else None,
        "location": row["location"] or "India",
        "job_type": row["employment_type"] or "Full-time",
        "employment_type": row["employment_type"] or "Full-time",
        "experience": row["experience"] if row["experience"] is not None else "",
        "salary": row["salary"] if (row["salary"] and str(row["salary"]).lower() not in ["none", "null", ""]) else "Not mentioned",
        "match_percent": int(row["match_score"]) if row["match_score"] is not None else 0,
        "match_score": int(row["match_score"]) if row["match_score"] is not None else 0,
        "openings": row["openings"] if ("openings" in row.keys() and row["openings"] and str(row["openings"]).strip()) else "NA",
        "posted_at": row["posted_time"] or "",
        "posted_time": row["posted_time"] or "",
        "apply_link": row["application_url"] or "",
        "application_url": row["application_url"] or "",
        "description": row["job_description"] or "",
        "source": row["source"] or "Google Jobs / SerpAPI",
        "saved_at": row["saved_at"] if "saved_at" in row.keys() else None,
        "applied_at": row["applied_at"] if "applied_at" in row.keys() else None,
    }


# --- SAVED JOBS CRUD ---

def save_job(user_id, job_data):
    """
    Saves a job for the authenticated user in the saved_jobs table.
    Ensures safe idempotency: repeated saves do not create duplicate rows.
    Preserves known openings when later updates do not have openings.
    Returns the deterministic job_id.
    """
    if not user_id or not job_data:
        return None

    norm = _normalize_job_payload(job_data)
    now_str = _now_iso()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO saved_jobs (
                user_id, job_id, job_title, company, company_logo, location,
                employment_type, experience, salary, match_score, posted_time,
                application_url, job_description, source, openings, saved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, job_id) DO UPDATE SET
                job_title = excluded.job_title,
                company = excluded.company,
                company_logo = excluded.company_logo,
                location = excluded.location,
                employment_type = excluded.employment_type,
                experience = excluded.experience,
                salary = excluded.salary,
                match_score = excluded.match_score,
                posted_time = excluded.posted_time,
                application_url = excluded.application_url,
                job_description = excluded.job_description,
                source = excluded.source,
                openings = CASE WHEN excluded.openings IS NOT NULL AND excluded.openings != '' THEN excluded.openings ELSE saved_jobs.openings END
        """, (
            user_id,
            norm["job_id"],
            norm["job_title"],
            norm["company"],
            norm["company_logo"],
            norm["location"],
            norm["employment_type"],
            norm["experience"],
            norm["salary"],
            norm["match_score"],
            norm["posted_time"],
            norm["application_url"],
            norm["job_description"],
            norm["source"],
            norm["openings"],
            now_str,
        ))
        conn.commit()
        return norm["job_id"]


def remove_saved_job(user_id, job_id):
    """
    Removes a saved job for a specific user from saved_jobs.
    Enforces strict user isolation: cannot remove another user's saved job.
    Returns True if a row was deleted, False otherwise.
    """
    if not user_id or not job_id:
        return False

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM saved_jobs
            WHERE user_id = ? AND job_id = ?
        """, (user_id, str(job_id).strip()))
        conn.commit()
        return cursor.rowcount > 0


def get_saved_jobs(user_id):
    """
    Retrieves all saved jobs for the given user_id, ordered by saved_at DESC.
    Returns list of job dictionaries.
    """
    if not user_id:
        return []

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM saved_jobs
            WHERE user_id = ?
            ORDER BY saved_at DESC, id DESC
        """, (user_id,))
        rows = cursor.fetchall()
        return [_row_to_job_dict(row) for row in rows]


def get_saved_job_ids(user_id):
    """
    Returns a set of all job_id strings saved by the given user_id.
    Used for O(1) membership checks during job listing rendering.
    """
    if not user_id:
        return set()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT job_id FROM saved_jobs
            WHERE user_id = ?
        """, (user_id,))
        rows = cursor.fetchall()
        return {row["job_id"] for row in rows}


def is_job_saved(user_id, job_id):
    """
    Checks whether a specific job is saved by the given user_id.
    """
    if not user_id or not job_id:
        return False

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM saved_jobs
            WHERE user_id = ? AND job_id = ?
            LIMIT 1
        """, (user_id, str(job_id).strip()))
        return cursor.fetchone() is not None


# --- APPLIED JOBS CRUD ---

def mark_job_applied(user_id, job_data):
    """
    Stores an applied job record for the authenticated user in applied_jobs table.
    Ensures safe idempotency: repeated clicks do not create duplicate rows.
    Preserves known openings when later updates do not have openings.
    Returns the deterministic job_id.
    """
    if not user_id or not job_data:
        return None

    norm = _normalize_job_payload(job_data)
    now_str = _now_iso()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO applied_jobs (
                user_id, job_id, job_title, company, company_logo, location,
                employment_type, experience, salary, match_score, posted_time,
                application_url, job_description, source, openings, applied_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, job_id) DO UPDATE SET
                job_title = excluded.job_title,
                company = excluded.company,
                company_logo = excluded.company_logo,
                location = excluded.location,
                employment_type = excluded.employment_type,
                experience = excluded.experience,
                salary = excluded.salary,
                match_score = excluded.match_score,
                posted_time = excluded.posted_time,
                application_url = excluded.application_url,
                job_description = excluded.job_description,
                source = excluded.source,
                openings = CASE WHEN excluded.openings IS NOT NULL AND excluded.openings != '' THEN excluded.openings ELSE applied_jobs.openings END
        """, (
            user_id,
            norm["job_id"],
            norm["job_title"],
            norm["company"],
            norm["company_logo"],
            norm["location"],
            norm["employment_type"],
            norm["experience"],
            norm["salary"],
            norm["match_score"],
            norm["posted_time"],
            norm["application_url"],
            norm["job_description"],
            norm["source"],
            norm["openings"],
            now_str,
        ))
        conn.commit()
        return norm["job_id"]


def mark_job_not_applied(user_id, job_id):
    """
    Removes the applied status for a specific job from applied_jobs for the user.
    Enforces strict user isolation: cannot modify another user's applied status.
    Returns True if a row was deleted, False otherwise.
    """
    if not user_id or not job_id:
        return False

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM applied_jobs
            WHERE user_id = ? AND job_id = ?
        """, (user_id, str(job_id).strip()))
        conn.commit()
        return cursor.rowcount > 0


def get_applied_jobs(user_id):
    """
    Retrieves all applied jobs for the given user_id, ordered by applied_at DESC.
    Returns list of job dictionaries.
    """
    if not user_id:
        return []

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM applied_jobs
            WHERE user_id = ?
            ORDER BY applied_at DESC, id DESC
        """, (user_id,))
        rows = cursor.fetchall()
        return [_row_to_job_dict(row) for row in rows]


def get_applied_job_ids(user_id):
    """
    Returns a set of all job_id strings marked as applied by the given user_id.
    Used for O(1) membership checks during job listing rendering.
    """
    if not user_id:
        return set()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT job_id FROM applied_jobs
            WHERE user_id = ?
        """, (user_id,))
        rows = cursor.fetchall()
        return {row["job_id"] for row in rows}


def is_job_applied(user_id, job_id):
    """
    Checks whether a specific job is marked as applied by the given user_id.
    """
    if not user_id or not job_id:
        return False

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM applied_jobs
            WHERE user_id = ? AND job_id = ?
            LIMIT 1
        """, (user_id, str(job_id).strip()))
        return cursor.fetchone() is not None


# --- JOB SEARCH RESULTS PERSISTENCE CRUD ---

def save_job_search_results(user_id, search_data):
    """
    Atomically saves and activates a complete normalized job search result set for a user.
    Preserves all normalized job data (job_id, title, company, logo, location, salary,
    experience, openings, match_score, matching_skills, posted_time, application_url, description, source).
    Uses a database transaction to ensure atomic replacement: marks previous searches
    as not current only when the new search successfully inserts.
    """
    if not user_id or not isinstance(search_data, dict):
        return None

    import uuid
    search_id = f"search_{uuid.uuid4().hex[:16]}"
    now_str = _now_iso()

    skills = search_data.get("skills") or []
    roles = search_data.get("roles") or []
    role_matches = search_data.get("role_matches") or []
    missing_skills = search_data.get("missing_skills") or {}
    market_insights = search_data.get("market_insights") or []
    jobs = search_data.get("jobs") or []

    skills_json = json.dumps(skills)
    roles_json = json.dumps(roles)
    role_matches_json = json.dumps(role_matches)
    missing_skills_json = json.dumps(missing_skills)
    market_insights_json = json.dumps(market_insights)
    jobs_data_json = json.dumps(jobs)
    total_jobs = len(jobs)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Atomic switch: set previous searches to is_current = 0
        cursor.execute("""
            UPDATE job_search_results
            SET is_current = 0, updated_at = ?
            WHERE user_id = ?
        """, (now_str, user_id))

        # Insert new active search
        cursor.execute("""
            INSERT INTO job_search_results (
                user_id, search_id, is_current, skills_json, roles_json,
                role_matches_json, missing_skills_json, market_insights_json,
                jobs_data_json, total_jobs, search_version, created_at, updated_at
            ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            user_id,
            search_id,
            skills_json,
            roles_json,
            role_matches_json,
            missing_skills_json,
            market_insights_json,
            jobs_data_json,
            total_jobs,
            now_str,
            now_str,
        ))
        conn.commit()
        return search_id


def get_current_job_search(user_id):
    """
    Retrieves the current active job search result set for the given authenticated user.
    Returns complete deserialized dictionary or None if no active search exists.
    """
    if not user_id:
        return None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, search_id, is_current, skills_json, roles_json,
                   role_matches_json, missing_skills_json, market_insights_json,
                   jobs_data_json, total_jobs, search_version, created_at, updated_at
            FROM job_search_results
            WHERE user_id = ? AND is_current = 1
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()

        if not row:
            return None

        try:
            skills = json.loads(row["skills_json"]) if row["skills_json"] else []
        except Exception:
            skills = []

        try:
            roles = json.loads(row["roles_json"]) if row["roles_json"] else []
        except Exception:
            roles = []

        try:
            role_matches = json.loads(row["role_matches_json"]) if row["role_matches_json"] else []
        except Exception:
            role_matches = []

        try:
            missing_skills = json.loads(row["missing_skills_json"]) if row["missing_skills_json"] else {}
        except Exception:
            missing_skills = {}

        try:
            market_insights = json.loads(row["market_insights_json"]) if row["market_insights_json"] else []
        except Exception:
            market_insights = []

        try:
            jobs = json.loads(row["jobs_data_json"]) if row["jobs_data_json"] else []
        except Exception:
            jobs = []

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "search_id": row["search_id"],
            "is_current": bool(row["is_current"]),
            "skills": skills,
            "roles": roles,
            "role_matches": role_matches,
            "missing_skills": missing_skills,
            "market_insights": market_insights,
            "jobs": jobs,
            "total_jobs": row["total_jobs"],
            "search_version": row["search_version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }


def has_current_job_search(user_id):
    """
    Fast boolean check if the user has an active persisted job search result.
    """
    if not user_id:
        return False

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM job_search_results
            WHERE user_id = ? AND is_current = 1
            LIMIT 1
        """, (user_id,))
        return cursor.fetchone() is not None


def delete_user_job_searches(user_id):
    """
    Deletes all saved job searches for a specific user.
    """
    if not user_id:
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM job_search_results WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount


# --- ATS ANALYSES PERSISTENT DATABASE STORAGE ---

def save_ats_analysis(
    user_id,
    filename,
    stored_filename,
    file_path,
    file_size,
    file_type,
    analysis_data
):
    """
    Atomically saves a new ATS analysis result for the authenticated user.
    Maintains only ONE active current ATS analysis per user.
    Safely deactivates old analysis and deletes old ATS file on filesystem only AFTER
    the new analysis is successfully committed.
    """
    if not user_id or not analysis_data:
        raise ValueError("user_id and analysis_data are required.")

    now_str = _now_iso()
    scores = analysis_data.get("scores", {})

    old_file_path = None
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Find previous active ATS resume file path to clean up safely
        cursor.execute("""
            SELECT file_path FROM ats_analyses
            WHERE user_id = ? AND is_current = 1
        """, (user_id,))
        old_rows = cursor.fetchall()
        if old_rows:
            old_file_path = old_rows[0]["file_path"]

        # Deactivate previous active records
        cursor.execute("""
            UPDATE ats_analyses
            SET is_current = 0
            WHERE user_id = ? AND is_current = 1
        """, (user_id,))

        # Insert fresh authoritative ATS analysis
        cursor.execute("""
            INSERT INTO ats_analyses (
                user_id, filename, stored_filename, file_path, file_type, file_size,
                uploaded_at, analyzed_at, final_score, ats_readability_score,
                content_quality_score, skills_score, experience_projects_score,
                completeness_score, quantification_score, grammar_consistency_score,
                score_message, score_status, primary_domain, parsed_sections_json,
                detected_skills_json, industry_terms_json, strengths_json,
                problems_detected_json, missing_sections_json, weak_bullets_json,
                recommendations_json, consistency_findings_json, analysis_json, is_current
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            user_id,
            filename,
            stored_filename,
            file_path,
            file_type.lower() if file_type else "pdf",
            file_size or 0,
            now_str,
            now_str,
            analysis_data.get("final_score", 0),
            scores.get("ats_readability", 0),
            scores.get("content_quality", 0),
            scores.get("skills", 0),
            scores.get("experience_projects", 0),
            scores.get("completeness", 0),
            scores.get("quantification", 0),
            scores.get("grammar_consistency", 0),
            analysis_data.get("score_message", ""),
            analysis_data.get("score_status", ""),
            analysis_data.get("primary_domain", "Full Stack Development"),
            json.dumps(analysis_data.get("parsed_sections_summary", {})),
            json.dumps(analysis_data.get("detected_skills", [])),
            json.dumps({
                "top_matched_keywords": analysis_data.get("top_matched_keywords", []),
                "missing_keywords": analysis_data.get("missing_keywords", [])
            }),
            json.dumps(analysis_data.get("strengths", [])),
            json.dumps(analysis_data.get("problems_detected", [])),
            json.dumps(analysis_data.get("missing_sections", [])),
            json.dumps(analysis_data.get("weak_bullets", [])),
            json.dumps(analysis_data.get("recommendations", [])),
            json.dumps(analysis_data.get("consistency_findings", [])),
            json.dumps(analysis_data),
        ))
        new_id = cursor.lastrowid
        conn.commit()

    # Clean up old physical file if it exists and is different from new file
    if old_file_path and old_file_path != file_path and os.path.exists(old_file_path):
        # Safety guard: never delete if it's the main_resume file
        if not os.path.basename(old_file_path).startswith("main_resume"):
            try:
                os.remove(old_file_path)
            except Exception as e:
                print(f"[ATS Cleanup] Notice removing old file {old_file_path}: {e}")

    return new_id


def get_latest_ats_analysis(user_id):
    """
    Retrieves the latest active ATS analysis for the authenticated user.
    Returns complete parsed dictionary or None if no ATS analysis has been done.
    """
    if not user_id:
        return None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, user_id, filename, stored_filename, file_path, file_type,
                   file_size, uploaded_at, analyzed_at, final_score,
                   ats_readability_score, content_quality_score, skills_score,
                   experience_projects_score, completeness_score,
                   quantification_score, grammar_consistency_score, score_message,
                   score_status, primary_domain, parsed_sections_json, detected_skills_json,
                   industry_terms_json, strengths_json, problems_detected_json,
                   missing_sections_json, weak_bullets_json, recommendations_json,
                   consistency_findings_json, analysis_json, is_current
            FROM ats_analyses
            WHERE user_id = ? AND is_current = 1
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,))
        row = cursor.fetchone()

        if not row:
            return None

        # Deserialization helpers
        def _load_json(val, default):
            if not val:
                return default
            try:
                return json.loads(val)
            except Exception:
                return default

        industry_terms = _load_json(row["industry_terms_json"], {})
        top_matched_keywords = industry_terms.get("top_matched_keywords", [])
        missing_keywords = industry_terms.get("missing_keywords", [])

        full_analysis = _load_json(row["analysis_json"], {})
        scores_from_analysis = full_analysis.get("scores", {})
        quantification_score = (
            row["quantification_score"]
            if ("quantification_score" in row.keys() and row["quantification_score"] is not None)
            else scores_from_analysis.get("quantification", 0)
        )

        r_score = row["ats_readability_score"]
        c_score = row["content_quality_score"]
        s_score = row["skills_score"]
        e_score = row["experience_projects_score"]
        comp_score = row["completeness_score"]
        q_score = quantification_score
        g_score = row["grammar_consistency_score"]

        # Authoritative 7-Factor Score Integrity Validation Check
        computed_final_score = int(round(
            (r_score * 0.25)
            + (c_score * 0.20)
            + (s_score * 0.15)
            + (e_score * 0.10)
            + (comp_score * 0.15)
            + (q_score * 0.05)
            + (g_score * 0.10)
        ))
        computed_final_score = max(1, min(100, computed_final_score))

        final_score = row["final_score"]
        if final_score != computed_final_score:
            print(f"[ATS Score Integrity] Aligning stored final score ({final_score}) with authoritative computed formula ({computed_final_score}).")
            final_score = computed_final_score

        return {
            "id": row["id"],
            "user_id": row["user_id"],
            "filename": row["filename"],
            "stored_filename": row["stored_filename"],
            "file_path": row["file_path"],
            "file_type": row["file_type"],
            "file_size": row["file_size"],
            "uploaded_at": row["uploaded_at"],
            "analyzed_at": row["analyzed_at"],
            "final_score": final_score,
            "scores": {
                "ats_readability": r_score,
                "content_quality": c_score,
                "skills": s_score,
                "experience_projects": e_score,
                "completeness": comp_score,
                "quantification": q_score,
                "grammar_consistency": g_score,
            },
            "weights": full_analysis.get("weights", {
                "ats_readability": 25,
                "content_quality": 20,
                "skills": 15,
                "experience_projects": 10,
                "completeness": 15,
                "quantification": 5,
                "grammar_consistency": 10
            }),
            "evidence": full_analysis.get("evidence", {}),
            "disclaimer": full_analysis.get("disclaimer", "This ATS Resume Compatibility Score evaluates resume structure, ATS readability, content quality, skills, projects/experience, completeness, quantification, and consistency. It is not a probability of getting shortlisted."),
            "score_message": row["score_message"],
            "score_status": row["score_status"],
            "primary_domain": row["primary_domain"],
            "secondary_domains": full_analysis.get("secondary_domains", []),
            "detected_skills": _load_json(row["detected_skills_json"], []),
            "skills_by_category": full_analysis.get("skills_by_category", {}),
            "top_matched_keywords": top_matched_keywords,
            "missing_keywords": missing_keywords,
            "demonstrated_and_listed": full_analysis.get("demonstrated_and_listed", []),
            "listed_not_demonstrated": full_analysis.get("listed_not_demonstrated", []),
            "demonstrated_not_listed": full_analysis.get("demonstrated_not_listed", []),
            "action_verb_analysis": full_analysis.get("action_verb_analysis", {}),
            "projects_analysis": full_analysis.get("projects_analysis", []),
            "bullet_optimizations": full_analysis.get("bullet_optimizations", []),
            "findings": full_analysis.get("findings", []),
            "strengths": _load_json(row["strengths_json"], []),
            "problems_detected": _load_json(row["problems_detected_json"], []),
            "missing_sections": _load_json(row["missing_sections_json"], []),
            "weak_bullets": _load_json(row["weak_bullets_json"], []),
            "recommendations": _load_json(row["recommendations_json"], []),
            "consistency_findings": _load_json(row["consistency_findings_json"], []),
            "ai_feedback_note": full_analysis.get("ai_feedback_note"),
            "contact_info": full_analysis.get("contact_info", {}),
            "contact_analysis": full_analysis.get("contact_analysis", {}),
            "summary_analysis": full_analysis.get("summary_analysis", {}),
            "candidate_name": full_analysis.get("candidate_name", ""),
            "full_analysis": full_analysis
        }


def delete_user_ats_analyses(user_id):
    """
    Deletes all ATS analyses for a specific user and removes associated files.
    """
    if not user_id:
        return 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT file_path FROM ats_analyses WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        for r in rows:
            fp = r["file_path"]
            if fp and os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception:
                    pass

        cursor.execute("DELETE FROM ats_analyses WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount


# --- COURSE PROGRESS & TOPIC COMPLETION CRUD ---

def set_topic_completion(user_id, course_id, topic_id, completed=True, last_watched=True):
    """
    Persists completion state and watch timestamps for a specific course topic per authenticated user.
    Strictly isolated by user_id, course_id, and topic_id.
    """
    if not user_id or not course_id or not topic_id:
        return None
    
    cid = str(course_id).strip().lower()
    tid = str(topic_id).strip().lower()
    now = _now_iso()
    comp_int = 1 if completed else 0
    comp_at = now if completed else None
    watched_at = now if last_watched else None

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO course_topic_progress (
                user_id, course_id, topic_id, completed, completed_at, last_watched_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, course_id, topic_id) DO UPDATE SET
                completed = excluded.completed,
                completed_at = CASE
                    WHEN excluded.completed = 1 THEN coalesce(course_topic_progress.completed_at, excluded.completed_at)
                    ELSE NULL
                END,
                last_watched_at = coalesce(excluded.last_watched_at, course_topic_progress.last_watched_at),
                updated_at = excluded.updated_at
        """, (user_id, cid, tid, comp_int, comp_at, watched_at, now, now))
        conn.commit()

    return get_course_progress_stats(user_id, cid)


def get_user_completed_topic_ids(user_id, course_id):
    """
    Returns a set of completed topic_ids for a given user and course.
    """
    if not user_id or not course_id:
        return set()
    cid = str(course_id).strip().lower()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT topic_id FROM course_topic_progress
            WHERE user_id = ? AND course_id = ? AND completed = 1
        """, (user_id, cid))
        rows = cursor.fetchall()
        return {str(r["topic_id"]).strip().lower() for r in rows}


def get_course_progress_stats(user_id, course_id, total_topics=None):
    """
    Calculates real, mathematically sound progress for a user on a given course.
    Formula: round((completed_topics / total_topics) * 100)
    0 completed = 0%
    all completed = 100%
    """
    if not user_id or not course_id:
        return {
            "completed_count": 0,
            "total_topics": total_topics or 0,
            "percentage": 0,
            "completed_ids": [],
        }

    cid = str(course_id).strip().lower()
    
    # If total_topics not supplied, determine from course_data if possible
    if total_topics is None:
        try:
            from course_data import get_course_topics
            topics = get_course_topics(cid)
            total_topics = len(topics) if topics else 0
        except Exception:
            total_topics = 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT topic_id, completed, completed_at, last_watched_at
            FROM course_topic_progress
            WHERE user_id = ? AND course_id = ?
        """, (user_id, cid))
        rows = cursor.fetchall()

    completed_ids = []
    for r in rows:
        if r["completed"] == 1:
            completed_ids.append(str(r["topic_id"]).strip().lower())

    completed_count = len(completed_ids)
    if total_topics and total_topics > 0:
        percentage = int(round((completed_count / float(total_topics)) * 100))
        # Ensure clamped between 0 and 100
        percentage = max(0, min(100, percentage))
    else:
        percentage = 0

    return {
        "completed_count": completed_count,
        "total_topics": total_topics,
        "percentage": percentage,
        "completed_ids": completed_ids,
    }


def get_all_courses_progress_for_user(user_id):
    """
    Returns progress stats dictionary for all courses for the given user.
    Keys are course_ids, values are progress dictionaries.
    """
    if not user_id:
        return {}

    try:
        from course_data import get_all_courses
        all_courses = get_all_courses()
    except Exception:
        all_courses = []

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT course_id, topic_id, completed
            FROM course_topic_progress
            WHERE user_id = ? AND completed = 1
        """, (user_id,))
        rows = cursor.fetchall()

    completed_by_course = {}
    for r in rows:
        cid = str(r["course_id"]).strip().lower()
        if cid not in completed_by_course:
            completed_by_course[cid] = set()
        completed_by_course[cid].add(str(r["topic_id"]).strip().lower())

    result = {}
    for course in all_courses:
        cid = course["id"].strip().lower()
        total_topics = len(course.get("topics", []))
        user_completed_set = completed_by_course.get(cid, set())
        completed_count = len(user_completed_set)
        if total_topics > 0:
            pct = int(round((completed_count / float(total_topics)) * 100))
            pct = max(0, min(100, pct))
        else:
            pct = 0
        result[cid] = {
            "course_id": cid,
            "completed_count": completed_count,
            "total_topics": total_topics,
            "percentage": pct,
            "completed_ids": list(user_completed_set),
        }
        # Also alias by slug if different
        slug = course.get("slug")
        if slug and slug != cid:
            result[slug] = result[cid]

    return result


def reset_user_course_progress(user_id, course_id=None):
    """
    Helper for testing or resetting user course progress.
    """
    if not user_id:
        return 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if course_id:
            cursor.execute("DELETE FROM course_topic_progress WHERE user_id = ? AND course_id = ?", (user_id, str(course_id).strip().lower()))
        else:
            cursor.execute("DELETE FROM course_topic_progress WHERE user_id = ?", (user_id,))
        conn.commit()
        return cursor.rowcount





