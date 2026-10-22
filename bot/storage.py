"""Archivio su SQLite: post generati, regole date in chat, CTA usate (base per le statistiche future)."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DATA_DIR / "bot.db", check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER, user_id INTEGER,
                source_url TEXT, source_owner TEXT,
                post_json TEXT, slide_paths TEXT,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT, user_id INTEGER, created_at TEXT, active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS cta_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER, tipo TEXT, manuale TEXT, testo TEXT, created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS post_stats (
                url TEXT PRIMARY KEY, owner TEXT, engagement REAL, published_at TEXT, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS rankings (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, data TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                chat_id INTEGER, message_id INTEGER, job_id INTEGER,
                PRIMARY KEY (chat_id, message_id)
            );
            """
        )
    return _conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------- post generati ----------------

def save_job(chat_id: int, user_id: int, source_url: str, owner: str,
             post: dict, slide_paths: list[Path]) -> int:
    with _lock:
        cur = _db().execute(
            "INSERT INTO jobs (chat_id, user_id, source_url, source_owner, post_json, slide_paths, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (chat_id, user_id, source_url, owner, json.dumps(post, ensure_ascii=False),
             json.dumps([str(p) for p in slide_paths]), _now(), _now()),
        )
        job_id = cur.lastrowid
        cta = post.get("cta") or {}
        _db().execute(
            "INSERT INTO cta_log (job_id, tipo, manuale, testo, created_at) VALUES (?,?,?,?,?)",
            (job_id, cta.get("tipo"), cta.get("manuale"), cta.get("frase"), _now()),
        )
        _db().commit()
        return job_id


def update_job(job_id: int, post: dict, slide_paths: list[Path]) -> None:
    with _lock:
        _db().execute(
            "UPDATE jobs SET post_json=?, slide_paths=?, updated_at=? WHERE id=?",
            (json.dumps(post, ensure_ascii=False), json.dumps([str(p) for p in slide_paths]), _now(), job_id),
        )
        _db().commit()


def get_job(job_id: int) -> dict | None:
    with _lock:
        row = _db().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row)


def last_job(chat_id: int) -> dict | None:
    """L'ultimo post mandato in questa chat (link manuale o report)."""
    with _lock:
        row = _db().execute(
            "SELECT job_id FROM messages WHERE chat_id=? ORDER BY message_id DESC LIMIT 1", (chat_id,)
        ).fetchone()
    return get_job(row["job_id"]) if row else None


def processed_urls(urls: list[str]) -> set[str]:
    """Quali di questi post sono già stati lavorati (per non riproporli nel report)."""
    if not urls:
        return set()
    with _lock:
        rows = _db().execute(
            f"SELECT source_url FROM jobs WHERE source_url IN ({','.join('?' * len(urls))})", urls
        ).fetchall()
    return {r["source_url"] for r in rows}


def _row_to_job(row) -> dict | None:
    if not row:
        return None
    return {
        "id": row["id"],
        "source_url": row["source_url"],
        "source_owner": row["source_owner"],
        "post": json.loads(row["post_json"]),
        "slide_paths": [Path(p) for p in json.loads(row["slide_paths"])],
    }


def recent_style(n: int = 6) -> dict:
    """Hook e autori usati negli ultimi post, per non ripetersi."""
    with _lock:
        rows = _db().execute("SELECT post_json FROM jobs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    hooks, authors = [], []
    for r in rows:
        p = json.loads(r["post_json"])
        if p.get("tipo_hook"):
            hooks.append(p["tipo_hook"])
        authors.extend(p.get("autori_citati") or [])
    return {"hook": hooks, "autori": list(dict.fromkeys(authors))}


# ---------------- collegamento messaggi -> post (per rispondere a un post preciso) ----------------

def link_message(chat_id: int, message_id: int, job_id: int) -> None:
    with _lock:
        _db().execute("INSERT OR REPLACE INTO messages VALUES (?,?,?)", (chat_id, message_id, job_id))
        _db().commit()


def job_for_message(chat_id: int, message_id: int) -> int | None:
    with _lock:
        row = _db().execute(
            "SELECT job_id FROM messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)
        ).fetchone()
    return row["job_id"] if row else None


# ---------------- regole date in chat ----------------

def add_rule(text: str, user_id: int) -> int:
    with _lock:
        cur = _db().execute(
            "INSERT INTO rules (text, user_id, created_at) VALUES (?,?,?)", (text, user_id, _now())
        )
        _db().commit()
        return cur.lastrowid


def list_rules() -> list[dict]:
    with _lock:
        rows = _db().execute("SELECT id, text FROM rules WHERE active=1 ORDER BY id").fetchall()
    return [{"id": r["id"], "text": r["text"]} for r in rows]


def remove_rule(rule_id: int) -> bool:
    with _lock:
        cur = _db().execute("UPDATE rules SET active=0 WHERE id=? AND active=1", (rule_id,))
        _db().commit()
        return cur.rowcount > 0


# ---------------------------------------------------------------- statistiche per la media storica

def save_post_stats(rows: list[tuple[str, str, float, str]]) -> None:
    """rows = (url, owner, engagement, published_at). Aggiorna i numeri se il post è già noto."""
    with _lock:
        _db().executemany(
            "INSERT INTO post_stats (url, owner, engagement, published_at, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(url) DO UPDATE SET engagement=excluded.engagement, updated_at=excluded.updated_at",
            [(u, o, e, ts, _now()) for u, o, e, ts in rows],
        )
        _db().commit()


def page_engagements(owner: str, since_iso: str, exclude_after_iso: str) -> list[float]:
    """Rendimento dei post di una pagina pubblicati tra since e exclude_after (i post troppo
    recenti non hanno ancora finito di raccogliere interazioni e abbasserebbero la media)."""
    with _lock:
        rows = _db().execute(
            "SELECT engagement FROM post_stats WHERE owner=? AND published_at>=? AND published_at<?",
            (owner, since_iso, exclude_after_iso),
        ).fetchall()
    return [r["engagement"] for r in rows]


def save_ranking(items: list[dict]) -> None:
    with _lock:
        _db().execute("INSERT INTO rankings (created_at, data) VALUES (?,?)",
                      (_now(), json.dumps(items, ensure_ascii=False)))
        _db().commit()


def last_ranking() -> list[dict]:
    with _lock:
        row = _db().execute("SELECT data FROM rankings ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["data"]) if row else []
