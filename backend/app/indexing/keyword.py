"""Keyword / symbol search: SQLite FTS5 with BM25 ranking, tuned for source code."""
import re
import sqlite3
import threading
from pathlib import Path

from app.config import get_settings
from app.indexing.chunker import Chunk

_WORD = re.compile(r"[A-Za-z0-9_]+")
_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")

# Question words and glue that carry no signal when matching code.
STOPWORDS = frozenset("""
a an the is are was were be been it its this that these those of to in on at by for from with as and or not
where what which who whom when why how do does did can could should would we you i my our your me us
there here has have had into out about than then so if else any all some
""".split())


def expand(text: str) -> str:
    """Token stream for indexing/querying: every identifier plus its camelCase / snake_case parts,
    so that `streamChat`, `stream_chat` and "stream chat" all find each other."""
    out: list[str] = []
    for match in _WORD.finditer(text):
        word = match.group()
        parts = _PARTS.findall(word)
        out.append(word)
        if len(parts) > 1:
            out.extend(parts)
    return " ".join(out)


def query_tokens(query: str) -> list[str]:
    seen: dict[str, None] = {}
    for token in expand(query).lower().split():
        token = token.strip("_")
        if len(token) >= 2 and token not in STOPWORDS:
            seen.setdefault(token)
    return list(seen)


class KeywordIndex:
    """Thread-safe FTS5 index shared by all repositories (rows are tagged with the collection name)."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.RLock()
        try:
            with self._lock, self._db:
                self._db.execute("PRAGMA journal_mode=WAL")
                self._db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS kw_fts USING fts5("
                    "name, signature, doc, path, text, "
                    "collection UNINDEXED, chunk_id UNINDEXED, category UNINDEXED, kind UNINDEXED, "
                    "tokenize='porter unicode61 remove_diacritics 2')")
                self._db.execute(
                    "CREATE TABLE IF NOT EXISTS kw_ids ("
                    "collection TEXT NOT NULL, chunk_id TEXT NOT NULL, fts_rowid INTEGER NOT NULL, "
                    "embed_hash TEXT NOT NULL, PRIMARY KEY (collection, chunk_id))")
        except sqlite3.OperationalError as e:
            raise RuntimeError(f"This Python's SQLite lacks FTS5, which keyword search needs: {e}") from e

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def count(self, collection: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM kw_ids WHERE collection = ?", (collection,)).fetchone()[0]

    def state(self, collection: str) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute("SELECT chunk_id, embed_hash FROM kw_ids WHERE collection = ?", (collection,))
            return dict(rows.fetchall())

    def upsert(self, collection: str, chunks: list[Chunk], hashes: list[str]) -> None:
        with self._lock, self._db:
            for chunk, embed_hash in zip(chunks, hashes):
                row = self._db.execute(
                    "SELECT fts_rowid FROM kw_ids WHERE collection = ? AND chunk_id = ?", (collection, chunk.id)).fetchone()
                if row:
                    self._db.execute("DELETE FROM kw_fts WHERE rowid = ?", (row[0],))
                cursor = self._db.execute(
                    "INSERT INTO kw_fts(name, signature, doc, path, text, collection, chunk_id, category, kind) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (expand(chunk.name), expand(chunk.signature or ""), expand(chunk.doc or ""), expand(chunk.path),
                     expand(chunk.text), collection, chunk.id, chunk.category, chunk.kind))
                self._db.execute(
                    "INSERT OR REPLACE INTO kw_ids(collection, chunk_id, fts_rowid, embed_hash) VALUES (?,?,?,?)",
                    (collection, chunk.id, cursor.lastrowid, embed_hash))

    def delete(self, collection: str, chunk_ids: list[str]) -> None:
        with self._lock, self._db:
            for i in range(0, len(chunk_ids), 400):
                batch = chunk_ids[i : i + 400]
                marks = ",".join("?" * len(batch))
                rowids = [r[0] for r in self._db.execute(
                    f"SELECT fts_rowid FROM kw_ids WHERE collection = ? AND chunk_id IN ({marks})", (collection, *batch))]
                self._db.executemany("DELETE FROM kw_fts WHERE rowid = ?", [(r,) for r in rowids])
                self._db.execute(f"DELETE FROM kw_ids WHERE collection = ? AND chunk_id IN ({marks})", (collection, *batch))

    def drop(self, collection: str) -> None:
        with self._lock, self._db:
            rowids = [r[0] for r in self._db.execute("SELECT fts_rowid FROM kw_ids WHERE collection = ?", (collection,))]
            self._db.executemany("DELETE FROM kw_fts WHERE rowid = ?", [(r,) for r in rowids])
            self._db.execute("DELETE FROM kw_ids WHERE collection = ?", (collection,))

    def search(self, collection: str, query: str, limit: int, categories: list[str] | None = None,
               kinds: list[str] | None = None) -> list[tuple[str, float]]:
        """(chunk_id, score) best first. Score is -bm25, so higher is better. Matching any word is enough;
        BM25 rewards chunks that match more of them, rarer ones, and matches in the name or signature."""
        tokens = query_tokens(query)
        if not tokens:
            return []
        match = " OR ".join(f'"{t}"' for t in tokens)
        sql = ("SELECT chunk_id, bm25(kw_fts, 8.0, 4.0, 3.0, 2.0, 1.0) AS score FROM kw_fts "
               "WHERE kw_fts MATCH ? AND collection = ?")
        params: list[object] = [match, collection]
        for column, values in (("category", categories), ("kind", kinds)):
            if values:
                sql += f" AND {column} IN ({','.join('?' * len(values))})"
                params.extend(values)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit)
        with self._lock:
            return [(cid, -score) for cid, score in self._db.execute(sql, params)]


_index: KeywordIndex | None = None
_index_lock = threading.Lock()


def get_keyword_index() -> KeywordIndex:
    global _index
    with _index_lock:
        if _index is None:
            _index = KeywordIndex(get_settings().data_dir / "keyword.sqlite")
        return _index


def close_keyword_index() -> None:
    global _index
    with _index_lock:
        if _index is not None:
            _index.close()
            _index = None