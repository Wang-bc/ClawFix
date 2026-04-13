from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.config.settings import Settings
from app.llm.client import LLMClient


logger = logging.getLogger("clawfix")

PARSER_VERSION = "retrieval-v2"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.:/-]+|[\u4e00-\u9fff]{2,}")
LEXICAL_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")
SEARCH_STOPWORDS = {
    "error",
    "errors",
    "exception",
    "exceptions",
    "trace",
    "traceback",
    "stacktrace",
    "stack",
    "thread",
    "main",
    "public",
    "class",
    "static",
    "void",
    "string",
    "import",
    "list",
    "item",
    "null",
}
EXACT_TERM_PATTERN = re.compile(r"[A-Za-z0-9_.:/-]*[0-9A-Z_.:/-][A-Za-z0-9_.:/-]*")


@dataclass(slots=True)
class SearchHit:
    title: str
    path: str
    snippet: str
    score: float
    source_type: str
    line_start: int
    line_end: int
    matched_terms: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "path": self.path,
            "snippet": self.snippet,
            "score": self.score,
            "source_type": self.source_type,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "matched_terms": self.matched_terms,
        }


@dataclass(slots=True)
class DocumentChunk:
    doc_id: str
    source_id: str
    domain: str
    title: str
    path: str
    snippet: str
    content: str
    source_type: str
    line_start: int
    line_end: int
    session_key: str = ""
    heading_path: str = ""
    token_count: int = 0


@dataclass(slots=True)
class SourceSnapshot:
    source_id: str
    domain: str
    source_type: str
    checksum: str
    path: str = ""
    session_key: str = ""
    content: str = ""
    title: str = ""
    session_items: tuple[dict[str, object], ...] = ()


@dataclass(slots=True)
class QueryPlan:
    raw_query: str
    tokens: list[str]
    lexical_terms: list[str]
    significant_terms: list[str]
    exact_terms: list[str]


@dataclass(slots=True)
class RetrievalCandidate:
    chunk: DocumentChunk
    score: float
    matched_terms: int
    exact_matches: int
    title_matches: int
    vector_score: float = 0.0
    route_hits: int = 0


class LexicalIndexStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._ready = False
        self._fts_available = True

    @property
    def fts_available(self) -> bool:
        return self._fts_available

    def ensure_ready(self) -> None:
        if self._ready:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    domain TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    path TEXT NOT NULL DEFAULT '',
                    session_key TEXT NOT NULL DEFAULT '',
                    checksum TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    embedding_fingerprint TEXT NOT NULL DEFAULT '',
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    doc_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    session_key TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    heading_path TEXT NOT NULL DEFAULT '',
                    snippet TEXT NOT NULL,
                    content TEXT NOT NULL,
                    line_start INTEGER NOT NULL,
                    line_end INTEGER NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_source_id ON chunks(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_domain ON chunks(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_session_key ON chunks(session_key)")
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                        doc_id UNINDEXED,
                        domain UNINDEXED,
                        session_key UNINDEXED,
                        title,
                        heading_path,
                        content,
                        tokenize='unicode61'
                    )
                    """
                )
            except sqlite3.OperationalError:
                logger.exception("SQLite FTS5 is unavailable; lexical search will fall back to exact matching")
                self._fts_available = False
        self._ready = True

    def list_sources(self) -> dict[str, dict[str, object]]:
        self.ensure_ready()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sources").fetchall()
        return {str(row["source_id"]): dict(row) for row in rows}

    def load_source_chunks(self, source_id: str) -> list[DocumentChunk]:
        self.ensure_ready()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE source_id = ? ORDER BY path, line_start, doc_id",
                (source_id,),
            ).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def load_chunks(self, *, domain: str, session_key: str = "") -> list[DocumentChunk]:
        self.ensure_ready()
        query = "SELECT * FROM chunks WHERE domain = ?"
        params: list[object] = [domain]
        if session_key:
            query += " AND session_key = ?"
            params.append(session_key)
        query += " ORDER BY path, line_start, doc_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def replace_source(
        self,
        snapshot: SourceSnapshot,
        chunks: list[DocumentChunk],
        *,
        parser_version: str,
        embedding_fingerprint: str,
    ) -> None:
        self.ensure_ready()
        with self._connect() as conn:
            doc_ids = [row[0] for row in conn.execute("SELECT doc_id FROM chunks WHERE source_id = ?", (snapshot.source_id,)).fetchall()]
            if doc_ids:
                placeholders = ",".join("?" for _ in doc_ids)
                conn.execute(f"DELETE FROM chunks WHERE doc_id IN ({placeholders})", doc_ids)
                if self._fts_available:
                    conn.execute(f"DELETE FROM chunks_fts WHERE doc_id IN ({placeholders})", doc_ids)
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO chunks (
                        doc_id, source_id, domain, source_type, session_key, path, title,
                        heading_path, snippet, content, line_start, line_end, token_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.doc_id,
                        chunk.source_id,
                        chunk.domain,
                        chunk.source_type,
                        chunk.session_key,
                        chunk.path,
                        chunk.title,
                        chunk.heading_path,
                        chunk.snippet,
                        chunk.content,
                        chunk.line_start,
                        chunk.line_end,
                        chunk.token_count,
                    ),
                )
                if self._fts_available:
                    conn.execute(
                        """
                        INSERT INTO chunks_fts (doc_id, domain, session_key, title, heading_path, content)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            chunk.doc_id,
                            chunk.domain,
                            chunk.session_key,
                            chunk.title,
                            chunk.heading_path,
                            chunk.content,
                        ),
                    )
            conn.execute(
                """
                INSERT INTO sources (
                    source_id, domain, source_type, path, session_key, checksum,
                    parser_version, embedding_fingerprint, chunk_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(source_id) DO UPDATE SET
                    domain = excluded.domain,
                    source_type = excluded.source_type,
                    path = excluded.path,
                    session_key = excluded.session_key,
                    checksum = excluded.checksum,
                    parser_version = excluded.parser_version,
                    embedding_fingerprint = excluded.embedding_fingerprint,
                    chunk_count = excluded.chunk_count,
                    updated_at = excluded.updated_at
                """,
                (
                    snapshot.source_id,
                    snapshot.domain,
                    snapshot.source_type,
                    snapshot.path,
                    snapshot.session_key,
                    snapshot.checksum,
                    parser_version,
                    embedding_fingerprint,
                    len(chunks),
                ),
            )

    def remove_source(self, source_id: str) -> None:
        self.ensure_ready()
        with self._connect() as conn:
            doc_ids = [row[0] for row in conn.execute("SELECT doc_id FROM chunks WHERE source_id = ?", (source_id,)).fetchall()]
            if doc_ids:
                placeholders = ",".join("?" for _ in doc_ids)
                conn.execute(f"DELETE FROM chunks WHERE doc_id IN ({placeholders})", doc_ids)
                if self._fts_available:
                    conn.execute(f"DELETE FROM chunks_fts WHERE doc_id IN ({placeholders})", doc_ids)
            conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))

    def search_bm25(
        self,
        *,
        domain: str,
        fts_query: str,
        limit: int,
        weights: tuple[float, float, float],
        session_key: str = "",
    ) -> list[str]:
        self.ensure_ready()
        if not self._fts_available or not fts_query:
            return []
        title_weight, heading_weight, content_weight = weights
        query = f"""
            SELECT chunks.doc_id AS doc_id
            FROM chunks_fts
            JOIN chunks ON chunks.doc_id = chunks_fts.doc_id
            WHERE chunks_fts MATCH ? AND chunks.domain = ?
            {'AND chunks.session_key = ?' if session_key else ''}
            ORDER BY bm25(chunks_fts, {title_weight}, {heading_weight}, {content_weight}) ASC
            LIMIT ?
        """
        params: list[object] = [fts_query, domain]
        if session_key:
            params.append(session_key)
        params.append(limit)
        try:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
        except sqlite3.OperationalError:
            logger.exception("FTS query failed domain=%s session_key=%s", domain, session_key or "-")
            return []
        return [str(row["doc_id"]) for row in rows]

    def _row_to_chunk(self, row: sqlite3.Row) -> DocumentChunk:
        return DocumentChunk(
            doc_id=str(row["doc_id"]),
            source_id=str(row["source_id"]),
            domain=str(row["domain"]),
            title=str(row["title"]),
            path=str(row["path"]),
            snippet=str(row["snippet"]),
            content=str(row["content"]),
            source_type=str(row["source_type"]),
            line_start=int(row["line_start"]),
            line_end=int(row["line_end"]),
            session_key=str(row["session_key"]),
            heading_path=str(row["heading_path"]),
            token_count=int(row["token_count"]),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection


class MarkdownSearchEngine:
    def __init__(
        self,
        workspace_root: Path,
        settings: Settings | None = None,
        llm_client: LLMClient | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.settings = settings
        self.llm_client = llm_client
        self._knowledge_chunks: list[DocumentChunk] = []
        self._case_chunks: list[DocumentChunk] = []
        self._session_chunks: dict[str, list[DocumentChunk]] = {}
        self._indexed = False
        self._qdrant_client = None
        self._vector_init_error = ""
        self._last_vector_query_error = ""
        self._last_vector_index_error = ""
        self._using_local_qdrant_fallback = False
        self._vector_backend_mode = "disabled"
        self._lexical_store = LexicalIndexStore(self._retrieval_db_path)

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        return self.search_shared(query, limit=limit)

    def search_shared(self, query: str, limit: int = 5) -> list[SearchHit]:
        self.build_index()
        if not query.strip():
            return []
        plan = self._analyze_query(query)
        if not plan.lexical_terms and not plan.exact_terms:
            return []

        case_hits = self._search_domain(
            plan,
            self._case_chunks,
            domain="cases",
            collection_name=self._cases_collection_name,
            limit=max(limit, 4),
        )
        knowledge_hits = self._search_domain(
            plan,
            self._knowledge_chunks,
            domain="knowledge",
            collection_name=self._knowledge_collection_name,
            limit=max(limit, 4),
        )

        case_quota = min(limit, max(1, (limit + 1) // 2))
        knowledge_quota = max(0, limit - case_quota)
        return self._merge_domains(
            case_hits,
            knowledge_hits,
            limit=limit,
            case_quota=case_quota,
            knowledge_quota=knowledge_quota,
        )

    def search_session_memory(self, session_key: str, query: str, limit: int = 3) -> list[SearchHit]:
        self.build_index()
        current_session = self._main_session_key(session_key)
        chunks = self._session_chunks.get(current_session, [])
        if not chunks or not query.strip():
            return []
        plan = self._analyze_query(query)
        if not plan.lexical_terms and not plan.exact_terms:
            return []
        hits = self._search_domain(
            plan,
            chunks,
            domain="session_memory",
            collection_name=self._session_collection_name,
            limit=limit,
            session_key=current_session,
        )
        return hits[:limit]

    def load_document(self, relative_path: str, max_chars: int = 5000) -> dict[str, object]:
        path = (self.workspace_root / relative_path).resolve()
        if not str(path).startswith(str(self.workspace_root)):
            raise ValueError("reading files outside the workspace is not allowed")
        content = path.read_text(encoding="utf-8")
        return {
            "path": relative_path,
            "content": content[:max_chars],
            "truncated": len(content) > max_chars,
        }

    def build_index(self, force: bool = False) -> None:
        if self._indexed and not force:
            return
        self._lexical_store.ensure_ready()
        snapshots = self._discover_source_snapshots()
        current_ids = {snapshot.source_id for snapshot in snapshots}
        existing = self._lexical_store.list_sources()
        embedding_fingerprint = self._embedding_fingerprint()

        for snapshot in snapshots:
            record = existing.get(snapshot.source_id)
            needs_update = (
                force
                or record is None
                or str(record.get("checksum", "")) != snapshot.checksum
                or str(record.get("parser_version", "")) != PARSER_VERSION
                or (embedding_fingerprint and str(record.get("embedding_fingerprint", "")) != embedding_fingerprint)
            )
            if not needs_update:
                continue
            previous_chunks = self._lexical_store.load_source_chunks(snapshot.source_id)
            current_chunks = self._build_chunks_for_snapshot(snapshot)
            self._lexical_store.replace_source(
                snapshot,
                current_chunks,
                parser_version=PARSER_VERSION,
                embedding_fingerprint=embedding_fingerprint,
            )
            self._sync_vector_chunks(
                domain=snapshot.domain,
                collection_name=self._collection_name_for_domain(snapshot.domain),
                previous_chunks=previous_chunks,
                current_chunks=current_chunks,
                session_key=snapshot.session_key,
            )

        for source_id, record in existing.items():
            if source_id in current_ids:
                continue
            previous_chunks = self._lexical_store.load_source_chunks(source_id)
            self._lexical_store.remove_source(source_id)
            self._sync_vector_chunks(
                domain=str(record.get("domain", "")) or "knowledge",
                collection_name=self._collection_name_for_domain(str(record.get("domain", "knowledge"))),
                previous_chunks=previous_chunks,
                current_chunks=[],
                session_key=str(record.get("session_key", "")),
            )

        self._reload_chunk_cache()
        self._ensure_vector_collections_ready()
        self._indexed = True

    def sync_case(self, path: Path) -> dict[str, object]:
        if not self._indexed:
            self.build_index()
        resolved = path.resolve()
        relative = resolved.relative_to(self.workspace_root).as_posix()
        source_id = f"cases:{relative}"
        previous = [chunk for chunk in self._case_chunks if chunk.source_id == source_id]
        if resolved.exists():
            snapshot = self._snapshot_for_document(resolved, domain="cases", source_type="Case")
            current = self._build_chunks_for_snapshot(snapshot)
            self._lexical_store.replace_source(
                snapshot,
                current,
                parser_version=PARSER_VERSION,
                embedding_fingerprint=self._embedding_fingerprint(),
            )
        else:
            current = []
            self._lexical_store.remove_source(source_id)
        self._reload_chunk_cache()
        return self._sync_vector_chunks(
            domain="cases",
            collection_name=self._cases_collection_name,
            previous_chunks=previous,
            current_chunks=current,
        )

    def sync_knowledge(self, path: Path) -> dict[str, object]:
        if not self._indexed:
            self.build_index()
        resolved = path.resolve()
        relative = resolved.relative_to(self.workspace_root).as_posix()
        source_id = f"knowledge:{relative}"
        previous = [chunk for chunk in self._knowledge_chunks if chunk.source_id == source_id]
        if resolved.exists():
            snapshot = self._snapshot_for_document(resolved, domain="knowledge", source_type="Knowledge")
            current = self._build_chunks_for_snapshot(snapshot)
            self._lexical_store.replace_source(
                snapshot,
                current,
                parser_version=PARSER_VERSION,
                embedding_fingerprint=self._embedding_fingerprint(),
            )
        else:
            current = []
            self._lexical_store.remove_source(source_id)
        self._reload_chunk_cache()
        return self._sync_vector_chunks(
            domain="knowledge",
            collection_name=self._knowledge_collection_name,
            previous_chunks=previous,
            current_chunks=current,
        )

    def sync_session_memory(self, session_key: str, items: list[dict[str, object]]) -> dict[str, object]:
        if not self._indexed:
            self.build_index()
        current_session = self._main_session_key(session_key)
        source_id = f"session_memory:{current_session}"
        previous = self._session_chunks.get(current_session, [])
        snapshot = self._snapshot_for_session_memory(current_session, items)
        current = self._build_chunks_for_snapshot(snapshot)
        if current:
            self._lexical_store.replace_source(
                snapshot,
                current,
                parser_version=PARSER_VERSION,
                embedding_fingerprint=self._embedding_fingerprint(),
            )
        else:
            self._lexical_store.remove_source(source_id)
        self._reload_chunk_cache()
        return self._sync_vector_chunks(
            domain="session_memory",
            collection_name=self._session_collection_name,
            previous_chunks=previous,
            current_chunks=current,
            session_key=current_session,
        )

    def vector_state(self) -> dict[str, object]:
        return {
            "enabled": self._vector_enabled(),
            "llm_enabled": bool(self.llm_client and self.llm_client.enabled),
            "embedding_model": self.settings.embedding_model if self.settings else "",
            "backend_mode": self._vector_backend_mode,
            "configured_mode": getattr(self.settings, "qdrant_mode", "auto") if self.settings else "auto",
            "qdrant_url": getattr(self.settings, "qdrant_url", "") if self.settings else "",
            "local_path": str(getattr(self.settings, "vector_store_dir", "")) if self.settings else "",
            "index_db_path": str(self._retrieval_db_path),
            "lexical_backend": "sqlite_fts5" if self._lexical_store.fts_available else "exact_fallback",
            "disabled_reason": self._vector_unavailable_reason(),
            "last_query_error": self._last_vector_query_error,
            "last_index_error": self._last_vector_index_error,
            "collections": {
                "knowledge": self._knowledge_collection_name,
                "cases": self._cases_collection_name,
                "session_memory": self._session_collection_name,
            },
        }

    @property
    def _knowledge_collection_name(self) -> str:
        if self.settings:
            return self.settings.qdrant_collection
        return "clawfix_knowledge"

    @property
    def _cases_collection_name(self) -> str:
        if self.settings:
            return self.settings.qdrant_cases_collection
        return "clawfix_cases"

    @property
    def _session_collection_name(self) -> str:
        if self.settings:
            return self.settings.qdrant_session_collection
        return "clawfix_session_memory"

    @property
    def _retrieval_db_path(self) -> Path:
        if self.settings:
            return self.settings.retrieval_db_path
        return (self.workspace_root / ".index" / "retrieval.db").resolve()

    def _discover_source_snapshots(self) -> list[SourceSnapshot]:
        snapshots: list[SourceSnapshot] = []
        for path in self._iter_knowledge_documents():
            snapshots.append(self._snapshot_for_document(path, domain="knowledge", source_type="Knowledge"))
        for path in self._iter_case_documents():
            snapshots.append(self._snapshot_for_document(path, domain="cases", source_type="Case"))
        for path in self._iter_session_meta_files():
            snapshot = self._snapshot_from_session_meta(path)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def _snapshot_for_document(self, path: Path, *, domain: str, source_type: str) -> SourceSnapshot:
        content = path.read_text(encoding="utf-8")
        relative = path.resolve().relative_to(self.workspace_root).as_posix()
        checksum = hashlib.sha1(content.encode("utf-8")).hexdigest()
        return SourceSnapshot(
            source_id=f"{domain}:{relative}",
            domain=domain,
            source_type=source_type,
            checksum=checksum,
            path=relative,
            content=content,
            title=self._extract_title(path, content),
        )

    def _snapshot_from_session_meta(self, path: Path) -> SourceSnapshot | None:
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        session_key = self._main_session_key(str(meta.get("session_key", "")).strip())
        agent_id = str(meta.get("agent_id", "")).strip()
        if not session_key or agent_id != "coordinator" or "::" in session_key:
            return None
        items = self._normalize_session_items(meta.get("durable_memory", []))
        checksum = hashlib.sha1(
            json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return SourceSnapshot(
            source_id=f"session_memory:{session_key}",
            domain="session_memory",
            source_type="Session Memory",
            checksum=checksum,
            session_key=session_key,
            path=f"session_memory/{hashlib.sha1(session_key.encode('utf-8')).hexdigest()[:12]}",
            session_items=tuple(items),
            title=f"Session {session_key}",
        )

    def _snapshot_for_session_memory(self, session_key: str, items: list[dict[str, object]]) -> SourceSnapshot:
        normalized_items = self._normalize_session_items(items)
        checksum = hashlib.sha1(
            json.dumps(normalized_items, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return SourceSnapshot(
            source_id=f"session_memory:{session_key}",
            domain="session_memory",
            source_type="Session Memory",
            checksum=checksum,
            session_key=session_key,
            path=f"session_memory/{hashlib.sha1(session_key.encode('utf-8')).hexdigest()[:12]}",
            session_items=tuple(normalized_items),
            title=f"Session {session_key}",
        )

    def _build_chunks_for_snapshot(self, snapshot: SourceSnapshot) -> list[DocumentChunk]:
        if snapshot.domain == "session_memory":
            return self._build_session_chunks(snapshot)
        return self._split_document(
            path=Path(snapshot.path),
            title=snapshot.title,
            source_type=snapshot.source_type,
            content=snapshot.content,
            source_id=snapshot.source_id,
            domain=snapshot.domain,
        )

    def _document_chunks(self, path: Path, *, source_type: str) -> list[DocumentChunk]:
        resolved = path.resolve()
        content = resolved.read_text(encoding="utf-8")
        domain = "cases" if source_type == "Case" else "knowledge"
        relative = resolved.relative_to(self.workspace_root).as_posix()
        return self._split_document(
            path=Path(relative),
            title=self._extract_title(resolved, content),
            source_type=source_type,
            content=content,
            source_id=f"{domain}:{relative}",
            domain=domain,
        )

    def _reload_chunk_cache(self) -> None:
        self._knowledge_chunks = self._lexical_store.load_chunks(domain="knowledge")
        self._case_chunks = self._lexical_store.load_chunks(domain="cases")
        self._session_chunks = {}
        for chunk in self._lexical_store.load_chunks(domain="session_memory"):
            self._session_chunks.setdefault(chunk.session_key, []).append(chunk)

    def _search_domain(
        self,
        plan: QueryPlan,
        chunks: list[DocumentChunk],
        *,
        domain: str,
        collection_name: str,
        limit: int,
        session_key: str = "",
    ) -> list[SearchHit]:
        if not chunks:
            return []
        route_limit = max(limit * 3, self._configured_route_limit(limit))
        route_results: list[tuple[str, list[str], float]] = []
        exact_hits = self._exact_match_route(plan, chunks, limit=route_limit)
        if exact_hits:
            route_results.append(("exact", exact_hits, 1.55))

        fts_query = self._build_fts_query(plan.lexical_terms)
        title_hits = self._lexical_store.search_bm25(
            domain=domain,
            session_key=session_key,
            fts_query=fts_query,
            limit=route_limit,
            weights=(4.8, 3.0, 1.0),
        )
        if title_hits:
            route_results.append(("bm25_title", title_hits, 1.25))
        body_hits = self._lexical_store.search_bm25(
            domain=domain,
            session_key=session_key,
            fts_query=fts_query,
            limit=route_limit,
            weights=(2.4, 1.7, 1.0),
        )
        if body_hits:
            route_results.append(("bm25_body", body_hits, 1.1))

        vector_scores = self._vector_search(
            plan.raw_query,
            collection_name=collection_name,
            limit=max(limit * 2, self.settings.vector_top_k if self.settings else 6),
            session_key=session_key,
        )
        if vector_scores:
            ordered_vector_hits = [doc_id for doc_id, _score in sorted(vector_scores.items(), key=lambda item: item[1], reverse=True)]
            route_results.append(("dense", ordered_vector_hits, 1.0 if domain != "session_memory" else 1.2))

        candidates = self._fuse_routes(route_results, chunks, vector_scores, plan)
        reranked = self._rerank_candidates(plan, candidates)
        return self._pack_hits(reranked, limit=limit, max_per_path=1 if domain == "session_memory" else 2)

    def _fuse_routes(
        self,
        route_results: list[tuple[str, list[str], float]],
        chunks: list[DocumentChunk],
        vector_scores: dict[str, float],
        plan: QueryPlan,
    ) -> list[RetrievalCandidate]:
        if not route_results:
            return []
        chunk_map = {chunk.doc_id: chunk for chunk in chunks}
        fused: dict[str, float] = {}
        route_hits: dict[str, int] = {}
        for _route_name, doc_ids, weight in route_results:
            for rank, doc_id in enumerate(doc_ids, start=1):
                if doc_id not in chunk_map:
                    continue
                fused[doc_id] = fused.get(doc_id, 0.0) + weight / (60.0 + rank)
                route_hits[doc_id] = route_hits.get(doc_id, 0) + 1

        candidates: list[RetrievalCandidate] = []
        for doc_id, fused_score in fused.items():
            chunk = chunk_map[doc_id]
            lowered_title = chunk.title.lower()
            lowered_heading = chunk.heading_path.lower()
            lowered_content = chunk.content.lower()
            matched_terms = sum(
                1 for token in plan.significant_terms if token in lowered_title or token in lowered_heading or token in lowered_content
            )
            exact_matches = sum(
                1 for token in plan.exact_terms if token.lower() in lowered_title or token.lower() in lowered_heading or token.lower() in lowered_content
            )
            title_matches = sum(1 for token in plan.significant_terms if token in lowered_title or token in lowered_heading)
            candidates.append(
                RetrievalCandidate(
                    chunk=chunk,
                    score=fused_score,
                    matched_terms=matched_terms,
                    exact_matches=exact_matches,
                    title_matches=title_matches,
                    vector_score=vector_scores.get(doc_id, 0.0),
                    route_hits=route_hits.get(doc_id, 0),
                )
            )
        return candidates

    def _rerank_candidates(self, plan: QueryPlan, candidates: list[RetrievalCandidate]) -> list[RetrievalCandidate]:
        reranked: list[RetrievalCandidate] = []
        total_terms = max(1, len(plan.significant_terms))
        for candidate in candidates:
            score = candidate.score
            score += 0.22 * min(candidate.matched_terms, total_terms) / total_terms
            score += 0.12 * candidate.title_matches
            score += 0.18 * candidate.exact_matches
            score += 0.06 * candidate.route_hits
            if candidate.chunk.source_type == "Session Memory" and candidate.matched_terms > 0:
                score += 0.08
            if candidate.chunk.source_type == "Case":
                score += 0.05
            if candidate.chunk.token_count > 420:
                score -= 0.05
            if candidate.matched_terms == 0 and candidate.exact_matches == 0 and candidate.vector_score < 0.45:
                continue
            reranked.append(
                RetrievalCandidate(
                    chunk=candidate.chunk,
                    score=score,
                    matched_terms=candidate.matched_terms,
                    exact_matches=candidate.exact_matches,
                    title_matches=candidate.title_matches,
                    vector_score=candidate.vector_score,
                    route_hits=candidate.route_hits,
                )
            )
        reranked.sort(key=lambda item: item.score, reverse=True)
        top_n = self.settings.rerank_top_n if self.settings else 12
        return reranked[:top_n]

    def _pack_hits(self, candidates: list[RetrievalCandidate], *, limit: int, max_per_path: int) -> list[SearchHit]:
        selected: list[SearchHit] = []
        path_counts: dict[str, int] = {}
        seen_ranges: set[tuple[str, int, int]] = set()
        for candidate in candidates:
            key = (candidate.chunk.path, candidate.chunk.line_start, candidate.chunk.line_end)
            if key in seen_ranges:
                continue
            path_counts[candidate.chunk.path] = path_counts.get(candidate.chunk.path, 0)
            if path_counts[candidate.chunk.path] >= max_per_path:
                continue
            seen_ranges.add(key)
            path_counts[candidate.chunk.path] += 1
            selected.append(
                SearchHit(
                    title=candidate.chunk.title,
                    path=candidate.chunk.path,
                    snippet=candidate.chunk.snippet,
                    score=round(candidate.score, 6),
                    source_type=candidate.chunk.source_type,
                    line_start=candidate.chunk.line_start,
                    line_end=candidate.chunk.line_end,
                    matched_terms=candidate.matched_terms,
                )
            )
            if len(selected) >= limit:
                break
        return selected

    def _merge_domains(
        self,
        case_hits: list[SearchHit],
        knowledge_hits: list[SearchHit],
        *,
        limit: int,
        case_quota: int,
        knowledge_quota: int,
    ) -> list[SearchHit]:
        selected = case_hits[:case_quota] + knowledge_hits[:knowledge_quota]
        seen = {(item.path, item.line_start, item.line_end) for item in selected}
        leftovers = [
            item
            for item in case_hits[case_quota:] + knowledge_hits[knowledge_quota:]
            if (item.path, item.line_start, item.line_end) not in seen
        ]
        leftovers.sort(key=lambda item: item.score, reverse=True)
        for item in leftovers:
            if len(selected) >= limit:
                break
            selected.append(item)
        selected.sort(key=lambda item: item.score, reverse=True)
        return selected[:limit]

    def _exact_match_route(self, plan: QueryPlan, chunks: list[DocumentChunk], *, limit: int) -> list[str]:
        terms = plan.exact_terms or plan.significant_terms[:4]
        if not terms:
            return []
        scored: list[tuple[float, str]] = []
        for chunk in chunks:
            lowered_title = chunk.title.lower()
            lowered_heading = chunk.heading_path.lower()
            lowered_path = chunk.path.lower()
            lowered_content = chunk.content.lower()
            score = 0.0
            for term in terms:
                lowered = term.lower()
                if lowered in lowered_title:
                    score += 5.0
                if lowered in lowered_heading:
                    score += 4.0
                if lowered in lowered_path:
                    score += 3.5
                if lowered in lowered_content:
                    score += 1.5
            if score > 0:
                scored.append((score, chunk.doc_id))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [doc_id for _score, doc_id in scored[:limit]]

    def _build_fts_query(self, lexical_terms: list[str]) -> str:
        escaped = []
        for term in lexical_terms:
            cleaned = term.replace('"', " ").strip()
            if cleaned:
                escaped.append(f'"{cleaned}"')
        return " OR ".join(escaped[:12])

    def _ensure_vector_collections_ready(self) -> None:
        if not self._vector_enabled():
            return
        client = self._get_qdrant_client()
        if client is None:
            return
        for collection_name in (
            self._knowledge_collection_name,
            self._cases_collection_name,
            self._session_collection_name,
        ):
            try:
                self._ensure_collection_ready(client, collection_name)
            except Exception as exc:  # noqa: BLE001
                self._last_vector_index_error = self._vector_error_label("vector_index_failed", exc)
                logger.exception("Vector collection ensure failed collection=%s", collection_name)
                break

    def _vector_search(
        self,
        query: str,
        *,
        collection_name: str,
        limit: int,
        session_key: str = "",
    ) -> dict[str, float]:
        try:
            client = self._get_qdrant_client()
            if client is None or self.llm_client is None:
                return {}
            self._ensure_collection_ready(client, collection_name)
            embedding = self.llm_client.embed_texts([query], model=self.settings.embedding_model if self.settings else None)[0]
            self._validate_embedding_dimensions([embedding], collection_name)
            query_filter = None
            if session_key:
                from qdrant_client import models as qmodels  # type: ignore

                query_filter = qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="session_key",
                            match=qmodels.MatchValue(value=session_key),
                        )
                    ]
                )
            response = self._query_collection(
                client,
                embedding=embedding,
                collection_name=collection_name,
                limit=limit,
                query_filter=query_filter,
            )
            results: dict[str, float] = {}
            points = getattr(response, "points", response) or []
            for point in points:
                payload = getattr(point, "payload", None) or {}
                doc_id = payload.get("doc_id")
                score = float(getattr(point, "score", 0.0) or 0.0)
                if isinstance(doc_id, str):
                    results[doc_id] = score
            self._last_vector_query_error = ""
            return results
        except Exception as exc:  # noqa: BLE001
            if self._switch_to_local_qdrant():
                return self._vector_search(query, collection_name=collection_name, limit=limit, session_key=session_key)
            self._last_vector_query_error = self._vector_error_label("vector_query_failed", exc)
            logger.exception(
                "Vector query failed collection=%s session_key=%s",
                collection_name,
                session_key or "-",
            )
            return {}

    def _query_collection(
        self,
        client,
        *,
        embedding: list[float],
        collection_name: str,
        limit: int,
        query_filter=None,
    ):
        if hasattr(client, "search"):
            search_kwargs: dict[str, object] = {
                "collection_name": collection_name,
                "query_vector": embedding,
                "limit": limit,
            }
            if query_filter is not None:
                search_kwargs["query_filter"] = query_filter
            return client.search(**search_kwargs)

        query_kwargs: dict[str, object] = {
            "collection_name": collection_name,
            "query": embedding,
            "limit": limit,
        }
        if query_filter is not None:
            query_kwargs["query_filter"] = query_filter
        return client.query_points(**query_kwargs)

    def _sync_vector_chunks(
        self,
        *,
        domain: str,
        collection_name: str,
        previous_chunks: list[DocumentChunk],
        current_chunks: list[DocumentChunk],
        session_key: str = "",
    ) -> dict[str, object]:
        result = {
            "domain": domain,
            "collection": collection_name,
            "chunk_count_before": len(previous_chunks),
            "chunk_count_after": len(current_chunks),
            "points_deleted": len(previous_chunks),
            "points_upserted": len(current_chunks),
            "vector_enabled": self._vector_enabled(),
            "vector_written": False,
            "backend_mode": self._vector_backend_mode,
            "disabled_reason": self._vector_unavailable_reason(),
            "embedding_model": self.settings.embedding_model if self.settings else "",
            "session_key": session_key,
            "collection_rebuilt": False,
            "lexical_written": True,
        }
        if not self._vector_enabled():
            return result
        try:
            client = self._get_qdrant_client()
            if client is None:
                result["disabled_reason"] = self._vector_unavailable_reason() or "qdrant_unavailable"
                return result
            if self._ensure_collection_ready(client, collection_name):
                result["vector_written"] = True
                result["collection_rebuilt"] = True
                result["disabled_reason"] = ""
                self._last_vector_index_error = ""
                self._upsert_chunks(client, collection_name, current_chunks)
                return result
            self._delete_points(client, collection_name, previous_chunks)
            self._upsert_chunks(client, collection_name, current_chunks)
            result["vector_written"] = True
            result["disabled_reason"] = ""
            self._last_vector_index_error = ""
            return result
        except Exception as exc:  # noqa: BLE001
            if self._switch_to_local_qdrant():
                return self._sync_vector_chunks(
                    domain=domain,
                    collection_name=collection_name,
                    previous_chunks=previous_chunks,
                    current_chunks=current_chunks,
                    session_key=session_key,
                )
            self._last_vector_index_error = self._vector_error_label("vector_index_failed", exc)
            result["disabled_reason"] = self._last_vector_index_error
            logger.exception(
                "Vector sync failed domain=%s collection=%s deleted=%s upserted=%s",
                domain,
                collection_name,
                result["points_deleted"],
                result["points_upserted"],
            )
            return result

    def _ensure_collection_ready(self, client, collection_name: str) -> bool:  # type: ignore[no-untyped-def]
        if not client.collection_exists(collection_name):
            self._create_collection(client, collection_name)
            self._restore_collection(client, collection_name)
            return True
        if self._collection_schema_matches(client, collection_name):
            return False
        logger.warning(
            "Vector collection schema mismatch collection=%s expected=%s; recreating",
            collection_name,
            self.settings.embedding_dimensions if self.settings else 1536,
        )
        self._recreate_collection(client, collection_name)
        self._restore_collection(client, collection_name)
        return True

    def _recreate_collection(self, client, collection_name: str) -> None:  # type: ignore[no-untyped-def]
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
        self._create_collection(client, collection_name)

    def _create_collection(self, client, collection_name: str) -> None:  # type: ignore[no-untyped-def]
        from qdrant_client import models as qmodels  # type: ignore

        client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=self.settings.embedding_dimensions if self.settings else 1536,
                distance=qmodels.Distance.COSINE,
            ),
        )

    def _restore_collection(self, client, collection_name: str) -> None:  # type: ignore[no-untyped-def]
        chunks = self._chunks_for_collection(collection_name)
        if chunks:
            self._upsert_chunks(client, collection_name, chunks)

    def _chunks_for_collection(self, collection_name: str) -> list[DocumentChunk]:
        if collection_name == self._knowledge_collection_name:
            return list(self._knowledge_chunks)
        if collection_name == self._cases_collection_name:
            return list(self._case_chunks)
        if collection_name == self._session_collection_name:
            return [chunk for chunks in self._session_chunks.values() for chunk in chunks]
        return []

    def _collection_schema_matches(self, client, collection_name: str) -> bool:  # type: ignore[no-untyped-def]
        if not self.settings:
            return True
        info = client.get_collection(collection_name)
        actual_size = int(getattr(info.config.params.vectors, "size", 0) or 0)
        expected_size = int(self.settings.embedding_dimensions or 0)
        return not expected_size or actual_size == expected_size

    def _upsert_chunks(self, client, collection_name: str, chunks: list[DocumentChunk]) -> None:  # type: ignore[no-untyped-def]
        if not chunks or self.llm_client is None:
            return
        from qdrant_client import models as qmodels  # type: ignore

        contents = [chunk.content for chunk in chunks]
        embeddings = self.llm_client.embed_texts(contents, model=self.settings.embedding_model if self.settings else None)
        self._validate_embedding_dimensions(embeddings, collection_name)
        points = []
        for chunk, vector in zip(chunks, embeddings):
            payload = {
                "doc_id": chunk.doc_id,
                "domain": chunk.domain,
                "title": chunk.title,
                "path": chunk.path,
                "heading_path": chunk.heading_path,
                "snippet": chunk.snippet,
                "source_type": chunk.source_type,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
            }
            if chunk.session_key:
                payload["session_key"] = chunk.session_key
            points.append(
                qmodels.PointStruct(
                    id=self._point_uuid(chunk.doc_id),
                    vector=vector,
                    payload=payload,
                )
            )
        if points:
            client.upsert(collection_name=collection_name, points=points, wait=True)

    def _validate_embedding_dimensions(self, embeddings: list[list[float]], collection_name: str) -> None:
        if not embeddings or not self.settings:
            return
        expected = int(self.settings.embedding_dimensions or 0)
        actual = len(embeddings[0])
        if expected > 0 and actual != expected:
            raise RuntimeError(
                f"embedding dimension mismatch for {collection_name}: expected {expected}, got {actual}"
            )

    def _delete_points(self, client, collection_name: str, chunks: list[DocumentChunk]) -> None:  # type: ignore[no-untyped-def]
        if not chunks:
            return
        from qdrant_client import models as qmodels  # type: ignore

        point_ids = [self._point_uuid(chunk.doc_id) for chunk in chunks]
        client.delete(
            collection_name=collection_name,
            points_selector=qmodels.PointIdsList(points=point_ids),
            wait=True,
        )

    def _get_qdrant_client(self):  # type: ignore[no-untyped-def]
        if self._qdrant_client is not None:
            return self._qdrant_client
        if not self._vector_enabled():
            self._vector_backend_mode = "disabled"
            return None

        try:
            from qdrant_client import QdrantClient  # type: ignore
        except ImportError:
            self._vector_backend_mode = "missing-client"
            self._vector_init_error = "missing-client"
            return None

        backend_mode = self._initial_qdrant_mode()
        try:
            if backend_mode == "remote" and self.settings and self.settings.qdrant_url:
                self._qdrant_client = QdrantClient(
                    url=self.settings.qdrant_url,
                    api_key=self.settings.qdrant_api_key or None,
                    check_compatibility=False,
                )
                self._vector_backend_mode = "remote"
            else:
                vector_store_dir = self.settings.vector_store_dir if self.settings else (self.workspace_root / ".qdrant").resolve()
                self._qdrant_client = QdrantClient(path=str(vector_store_dir))
                self._vector_backend_mode = "local"
            self._vector_init_error = ""
        except Exception:  # noqa: BLE001
            self._qdrant_client = None
            self._vector_backend_mode = "disabled"
            self._vector_init_error = "qdrant_init_failed"
            return None
        return self._qdrant_client

    def _vector_enabled(self) -> bool:
        return not self._vector_unavailable_reason()

    def _vector_unavailable_reason(self) -> str:
        if not self.settings:
            return "missing_settings"
        if not self.settings.enable_vector_search:
            return "vector_search_disabled"
        if self.llm_client is None:
            return "missing_llm_client"
        if not self.llm_client.enabled:
            return "llm_disabled"
        if self._vector_init_error:
            return self._vector_init_error
        return ""

    def _switch_to_local_qdrant(self) -> bool:
        if (
            self._using_local_qdrant_fallback
            or not self.settings
            or self._vector_backend_mode in {"local", "local-fallback"}
        ):
            return False
        try:
            from qdrant_client import QdrantClient  # type: ignore
        except ImportError:
            return False
        try:
            self._qdrant_client = QdrantClient(path=str(self.settings.vector_store_dir))
        except Exception:  # noqa: BLE001
            return False
        self._using_local_qdrant_fallback = True
        self._vector_backend_mode = "local-fallback"
        self._vector_init_error = ""
        return True

    def _vector_error_label(self, prefix: str, exc: Exception) -> str:
        detail = " ".join(str(exc).split())
        if not detail:
            return prefix
        return f"{prefix}: {detail[:180]}"

    def _initial_qdrant_mode(self) -> str:
        if not self.settings:
            return "local"
        mode = (self.settings.qdrant_mode or "auto").lower()
        if mode == "remote" and self.settings.qdrant_url:
            return "remote"
        if mode == "local":
            return "local"
        if self.settings.qdrant_url and self._running_in_docker():
            return "remote"
        return "local"

    def _running_in_docker(self) -> bool:
        return Path("/.dockerenv").exists() if Path("/").exists() else False

    def _split_document(
        self,
        *,
        path: Path,
        title: str,
        source_type: str,
        content: str,
        source_id: str,
        domain: str,
    ) -> list[DocumentChunk]:
        lines = content.splitlines()
        sections = self._partition_sections(lines, title=title)
        chunks: list[DocumentChunk] = []
        for heading_path, line_start, section_lines in sections:
            chunks.extend(
                self._chunk_section(
                    path=path,
                    title=title,
                    source_type=source_type,
                    source_id=source_id,
                    domain=domain,
                    heading_path=heading_path,
                    line_start=line_start,
                    section_lines=section_lines,
                )
            )
        if not chunks:
            body = content.strip()
            if body:
                chunks.append(
                    self._make_chunk(
                        source_id=source_id,
                        domain=domain,
                        title=title,
                        path=path.as_posix(),
                        source_type=source_type,
                        line_start=1,
                        line_end=max(1, len(lines)),
                        heading_path=title,
                        content=body,
                    )
                )
        return chunks

    def _partition_sections(self, lines: list[str], *, title: str) -> list[tuple[str, int, list[str]]]:
        headings: list[str] = [title] if title else []
        sections: list[tuple[str, int, list[str]]] = []
        current_lines: list[str] = []
        current_start = 1
        current_heading = title

        def flush() -> None:
            if current_lines:
                sections.append((current_heading or title, current_start, list(current_lines)))

        for index, line in enumerate(lines, start=1):
            match = HEADING_PATTERN.match(line)
            if match:
                flush()
                level = len(match.group(1))
                heading_text = match.group(2).strip() or title
                headings[:] = headings[: level - 1]
                headings.append(heading_text)
                current_heading = " > ".join(item for item in headings if item)
                current_lines = [line]
                current_start = index
                continue
            if not current_lines:
                current_start = index
                current_lines = [line]
            else:
                current_lines.append(line)
        flush()
        return sections

    def _chunk_section(
        self,
        *,
        path: Path,
        title: str,
        source_type: str,
        source_id: str,
        domain: str,
        heading_path: str,
        line_start: int,
        section_lines: list[str],
    ) -> list[DocumentChunk]:
        units = self._section_units(section_lines)
        if not units:
            return []
        chunks: list[DocumentChunk] = []
        current_units: list[tuple[str, int, int]] = []
        current_chars = 0
        target_chars = 1400
        max_chars = 1800

        def flush() -> None:
            nonlocal current_units, current_chars
            if not current_units:
                return
            start_line = line_start + current_units[0][1] - 1
            end_line = line_start + current_units[-1][2] - 1
            body = "\n\n".join(unit for unit, _start, _end in current_units).strip()
            if body:
                chunks.append(
                    self._make_chunk(
                        source_id=source_id,
                        domain=domain,
                        title=title,
                        path=path.as_posix(),
                        source_type=source_type,
                        line_start=start_line,
                        line_end=end_line,
                        heading_path=heading_path,
                        content=body,
                    )
                )
            overlap = current_units[-1:] if len(current_units) > 1 else []
            current_units = list(overlap)
            current_chars = sum(len(item[0]) for item in current_units)

        for unit_text, start_line_offset, end_line_offset in units:
            unit_chars = len(unit_text)
            if current_units and current_chars + unit_chars > max_chars:
                flush()
            current_units.append((unit_text, start_line_offset, end_line_offset))
            current_chars += unit_chars
            if current_chars >= target_chars:
                flush()
        flush()
        return chunks

    def _section_units(self, section_lines: list[str]) -> list[tuple[str, int, int]]:
        units: list[tuple[str, int, int]] = []
        buffer: list[str] = []
        unit_start = 1
        in_code_block = False
        for offset, line in enumerate(section_lines, start=1):
            stripped = line.strip()
            if stripped.startswith("```"):
                if not buffer:
                    unit_start = offset
                buffer.append(line)
                in_code_block = not in_code_block
                if not in_code_block:
                    units.append(("\n".join(buffer).strip(), unit_start, offset))
                    buffer = []
                continue
            if in_code_block:
                if not buffer:
                    unit_start = offset
                buffer.append(line)
                continue
            if not stripped:
                if buffer:
                    units.append(("\n".join(buffer).strip(), unit_start, offset - 1))
                    buffer = []
                continue
            if not buffer:
                unit_start = offset
            buffer.append(line)
        if buffer:
            units.append(("\n".join(buffer).strip(), unit_start, len(section_lines)))
        return [unit for unit in units if unit[0]]

    def _make_chunk(
        self,
        *,
        source_id: str,
        domain: str,
        title: str,
        path: str,
        source_type: str,
        line_start: int,
        line_end: int,
        heading_path: str,
        content: str,
        session_key: str = "",
        doc_key: str = "",
    ) -> DocumentChunk:
        normalized = self._normalize_text(content)
        doc_id = hashlib.sha1(
            (doc_key or f"{source_id}:{line_start}:{line_end}:{heading_path}:{normalized}").encode("utf-8")
        ).hexdigest()
        return DocumentChunk(
            doc_id=doc_id,
            source_id=source_id,
            domain=domain,
            title=title,
            path=path,
            snippet=normalized[:220],
            content=content[:3200],
            source_type=source_type,
            line_start=line_start,
            line_end=line_end,
            session_key=session_key,
            heading_path=heading_path,
            token_count=len(self._tokenize(content)),
        )

    def _build_session_chunks(self, snapshot: SourceSnapshot) -> list[DocumentChunk]:
        chunks: list[DocumentChunk] = []
        for index, item in enumerate(snapshot.session_items, start=1):
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            memory_id = str(item.get("memory_id", "")).strip()
            if not title or not content or not memory_id:
                continue
            chunks.append(
                self._make_chunk(
                    source_id=snapshot.source_id,
                    domain="session_memory",
                    title=title,
                    path=snapshot.path,
                    source_type="Session Memory",
                    line_start=index,
                    line_end=index,
                    heading_path=str(item.get("kind", "fact")).strip() or "fact",
                    content=content[:1800],
                    session_key=snapshot.session_key,
                    doc_key=f"{snapshot.source_id}:{memory_id}:{str(item.get('checksum', '')).strip()}",
                )
            )
        return chunks

    def _normalize_session_items(self, value: object) -> list[dict[str, object]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, object]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            content = str(item.get("content", "")).strip()
            memory_id = str(item.get("memory_id", "")).strip()
            checksum = str(item.get("checksum", "")).strip()
            created_at = str(item.get("created_at", "")).strip()
            updated_at = str(item.get("updated_at", "")).strip()
            kind = str(item.get("kind", "")).strip() or "fact"
            if not title or not content or not memory_id or not checksum:
                continue
            normalized.append(
                {
                    "memory_id": memory_id,
                    "kind": kind,
                    "title": title,
                    "content": content,
                    "checksum": checksum,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )
        return normalized

    def _point_uuid(self, doc_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, doc_id))

    def _tokenize(self, text: str) -> list[str]:
        tokens = [token.lower() for token in TOKEN_PATTERN.findall(text)]
        return [token for token in tokens if len(token) > 1]

    def _lexical_tokenize(self, text: str) -> list[str]:
        tokens = [token.lower() for token in LEXICAL_TOKEN_PATTERN.findall(text)]
        return [token for token in tokens if len(token) > 1]

    def _analyze_query(self, text: str) -> QueryPlan:
        tokens = self._tokenize(text)
        lexical_terms = []
        for token in self._lexical_tokenize(text):
            if token in SEARCH_STOPWORDS or len(token) < 2:
                continue
            if token not in lexical_terms:
                lexical_terms.append(token)
        significant_terms = []
        for token in lexical_terms:
            if len(token) < 3:
                continue
            if token not in significant_terms:
                significant_terms.append(token)
        exact_terms = []
        for token in TOKEN_PATTERN.findall(text):
            if len(token.strip()) < 3:
                continue
            lowered = token.lower()
            if lowered in SEARCH_STOPWORDS:
                continue
            is_exact = (
                EXACT_TERM_PATTERN.fullmatch(token) is not None
                and any(char.isdigit() or char in "._:/-" for char in token)
            ) or token.endswith(("Exception", "Error"))
            if is_exact and token not in exact_terms:
                exact_terms.append(token)
        if not exact_terms:
            exact_terms = significant_terms[:4]
        return QueryPlan(
            raw_query=text,
            tokens=tokens,
            lexical_terms=lexical_terms[:16],
            significant_terms=significant_terms[:12],
            exact_terms=exact_terms[:6],
        )

    def _configured_route_limit(self, limit: int) -> int:
        lexical_top_k = self.settings.lexical_top_k if self.settings else 12
        vector_top_k = self.settings.vector_top_k if self.settings else 6
        return max(limit * 3, lexical_top_k, vector_top_k)

    def _embedding_fingerprint(self) -> str:
        if not self._vector_enabled() or not self.settings:
            return ""
        return f"{self.settings.embedding_model}:{self.settings.embedding_dimensions}"

    def _extract_title(self, path: Path, content: str) -> str:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip() or path.stem
        return path.stem

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _main_session_key(self, session_key: str) -> str:
        return session_key.split("::", 1)[0]

    def _collection_name_for_domain(self, domain: str) -> str:
        if domain == "cases":
            return self._cases_collection_name
        if domain == "session_memory":
            return self._session_collection_name
        return self._knowledge_collection_name

    def _iter_knowledge_documents(self) -> Iterable[Path]:
        seen: set[Path] = set()
        candidates = [self.workspace_root / "MEMORY.md"]
        memory_dir = self._memory_dir
        if memory_dir.exists():
            candidates.extend(
                path
                for path in memory_dir.rglob("*")
                if path.is_file()
                and path.suffix.lower() in {".md", ".txt"}
                and "daily" not in path.relative_to(memory_dir).parts
            )
        knowledge_dir = self._knowledge_dir
        if knowledge_dir.exists():
            candidates.extend(
                path for path in knowledge_dir.rglob("*") if path.is_file() and path.suffix.lower() in {".md", ".txt"}
            )
        for path in candidates:
            resolved = path.resolve()
            if resolved.exists() and resolved.is_file() and resolved not in seen:
                seen.add(resolved)
                yield resolved

    def _iter_case_documents(self) -> Iterable[Path]:
        cases_dir = self._cases_dir
        if not cases_dir.exists():
            return []
        return sorted(path.resolve() for path in cases_dir.glob("*.md") if path.is_file())

    def _iter_session_meta_files(self) -> Iterable[Path]:
        sessions_dir = self._sessions_dir
        if not sessions_dir.exists():
            return []
        return sorted(path.resolve() for path in sessions_dir.rglob("*.meta.json") if path.is_file())

    @property
    def _memory_dir(self) -> Path:
        if self.settings:
            return self.settings.memory_dir
        return self.workspace_root / "memory"

    @property
    def _cases_dir(self) -> Path:
        if self.settings:
            return self.settings.cases_dir
        return self.workspace_root / "cases"

    @property
    def _knowledge_dir(self) -> Path:
        if self.settings:
            return self.settings.knowledge_dir
        return self.workspace_root / "knowledge"

    @property
    def _sessions_dir(self) -> Path:
        if self.settings:
            return self.settings.sessions_dir
        return self.workspace_root / ".sessions"
