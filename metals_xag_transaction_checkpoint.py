"""Private, additive cache for already validated immutable OANDA idranges.

Not an accounting authority; live broker ownership and cash reconciliation still
run before promotion. Cache failure/corruption falls back to bounded broker GETs.
No account identifier, broker credential, or payload is logged/exported.
"""
from contextlib import contextmanager
import hashlib
import json
import sqlite3

from metals_xag_ledger_audit import _id

TABLE = "metals_xag_transaction_checkpoint_v1"
FORMAT = "compact-transaction-pages-v1"
MAX_PAGE_IDS = 300


def valid_page(start, end, rows):
    if not isinstance(rows, list) or not 0 <= end - start < MAX_PAGE_IDS:
        return False
    ids = [_id(t.get("id")) if isinstance(t, dict) else None for t in rows]
    return (all(i is not None and start <= i <= end for i in ids)
            and len(ids) == len(set(ids)))


def scope(account, earliest):
    return hashlib.sha256(f"{FORMAT}:{account}:{earliest}".encode()).hexdigest()


def checksum(key, start, end, payload):
    return hashlib.sha256(f"{key}:{start}:{end}:".encode() + payload.encode()).hexdigest()


class Checkpoint:
    def __init__(self, get_conn):
        self.get_conn = get_conn

    @contextmanager
    def connection(self):
        conn = self.get_conn()
        try:
            with conn:
                # Avoid competing with live trading or a backup for a lock;
                # cache availability is never required for reconciliation.
                if isinstance(conn, sqlite3.Connection):
                    conn.execute("PRAGMA busy_timeout = 1000")
                elif getattr(conn, "driver_name", None) in ("psycopg", "psycopg2"):
                    conn.execute("SET LOCAL lock_timeout = '1s'")
                    conn.execute("SET LOCAL statement_timeout = '5s'")
                conn.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                    scope_key TEXT NOT NULL,
                    first_id BIGINT NOT NULL,
                    last_id BIGINT NOT NULL,
                    payload TEXT NOT NULL,
                    checksum_sha256 TEXT NOT NULL,
                    PRIMARY KEY (scope_key, first_id, last_id)
                )""")
                yield conn
        finally:
            # sqlite connection context managers commit but do not close.
            if isinstance(conn, sqlite3.Connection):
                conn.close()

    def load(self, account, earliest, last):
        key = scope(account, earliest)
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT first_id,last_id,payload,checksum_sha256 FROM {TABLE} "
                "WHERE scope_key=? AND first_id>=? AND last_id<=? "
                "ORDER BY first_id ASC,last_id DESC",
                (key, earliest, last)).fetchall()
        next_id = earliest
        pages = []
        for row in rows:
            try:
                start, end = int(row["first_id"]), int(row["last_id"])
                if start < next_id:  # alternative shorter already-covered page
                    continue
                if start != next_id:
                    break
                payload = row["payload"]
                if checksum(key,start,end,payload) != row["checksum_sha256"]:
                    break
                items = json.loads(payload)
                if not valid_page(start,end,items):
                    break
            except (KeyError, TypeError, ValueError):
                break
            pages.append((start,end,items))
            next_id = end + 1
        return pages

    def save(self, account, earliest, pages):
        key = scope(account, earliest)
        with self.connection() as conn:
            for start,end,items in pages:
                if not valid_page(start,end,items):
                    raise ValueError("invalid checkpoint page")
                payload = json.dumps(items, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False)
                conn.execute(
                    f"INSERT INTO {TABLE} "
                    "(scope_key,first_id,last_id,payload,checksum_sha256) VALUES (?,?,?,?,?) "
                    "ON CONFLICT (scope_key,first_id,last_id) DO UPDATE SET "
                    "payload=excluded.payload,checksum_sha256=excluded.checksum_sha256",
                    (key,start,end,payload,checksum(key,start,end,payload)))
