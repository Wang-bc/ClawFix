from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse


def _load_env_file(env_path: Path) -> None:
    """轻量加载 .env，避免强依赖 python-dotenv。"""
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        os.environ.setdefault(key, value)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(value: str | os.PathLike[str], *, base_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_llm_base_url(base_url: str, provider: str) -> str:
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        normalized = "https://api.openai.com/v1"
    if provider.lower() != "openai":
        return normalized

    parsed = urlparse(normalized)
    if parsed.path in {"", "/"}:
        parsed = parsed._replace(path="/v1")
        normalized = urlunparse(parsed)
    return normalized.rstrip("/")


def _running_in_docker() -> bool:
    return Path("/.dockerenv").exists() or os.getenv("RUNNING_IN_DOCKER", "").strip().lower() in {"1", "true"}


def _normalize_qdrant_url(qdrant_url: str) -> str:
    normalized = qdrant_url.strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.hostname == "qdrant" and not _running_in_docker():
        port = parsed.port or 6333
        scheme = parsed.scheme or "http"
        parsed = parsed._replace(netloc=f"127.0.0.1:{port}", scheme=scheme)
        normalized = urlunparse(parsed)
    return normalized


@dataclass(slots=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 3000
    workspace_dir: Path = Path("workspace")
    public_dir: Path = Path("public")

    enable_web_search: bool = True
    web_search_timeout_s: int = 8
    web_search_provider: str = "tavily"
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    tavily_search_depth: str = "advanced"
    tavily_extract_depth: str = "advanced"
    tavily_topic: str = "general"

    enable_llm: bool = False
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_style: str = "auto"
    llm_model: str = "gpt-4.1"
    llm_summary_model: str = "gpt-4.1-mini"
    llm_temperature: float = 0.2
    embedding_model: str = "text-embedding-3-small"
    embedding_fallback_models: tuple[str, ...] = ("text-embedding-3-small",)
    embedding_dimensions: int = 1536

    enable_vector_search: bool = True
    vector_backend: str = "qdrant"
    qdrant_mode: str = "auto"
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "clawfix_knowledge"
    qdrant_cases_collection: str = "clawfix_cases"
    qdrant_session_collection: str = "clawfix_session_memory"
    qdrant_local_path: str = ""
    vector_top_k: int = 6
    lexical_top_k: int = 12
    rerank_top_n: int = 12

    max_context_chars: int = 24000
    max_reply_chars: int = 4800
    max_search_results: int = 5
    max_delivery_retries: int = 4
    delivery_poll_interval_s: float = 2.0
    session_compact_threshold_chars: int = 16000

    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_connection_mode: str = "websocket"

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = _project_root()
        env_file_value = os.getenv("ENV_FILE", ".env")
        env_file = _resolve_path(env_file_value, base_dir=project_root)
        if not env_file.exists():
            fallback_env = Path.cwd() / ".env"
            if fallback_env.exists():
                env_file = fallback_env.resolve()
        _load_env_file(env_file)

        workspace_dir = _resolve_path(os.getenv("WORKSPACE_DIR", "./workspace"), base_dir=project_root)
        public_dir = _resolve_path(os.getenv("PUBLIC_DIR", "./public"), base_dir=project_root)
        qdrant_local = str(_resolve_path(os.getenv("QDRANT_LOCAL_PATH", "./workspace/.qdrant"), base_dir=project_root))

        llm_provider = os.getenv("LLM_PROVIDER", "openai").strip() or "openai"
        llm_base_url = _normalize_llm_base_url(
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            llm_provider,
        )
        qdrant_url = _normalize_qdrant_url(os.getenv("QDRANT_URL", "").strip())
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        enable_llm = _env_bool("ENABLE_LLM", bool(api_key))
        enable_vector_search = _env_bool("ENABLE_VECTOR_SEARCH", True)
        fallback_models = tuple(
            item.strip()
            for item in os.getenv("OPENAI_EMBEDDING_FALLBACK_MODELS", "text-embedding-3-small").split(",")
            if item.strip()
        )

        return cls(
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "3000")),
            workspace_dir=workspace_dir,
            public_dir=public_dir,
            enable_web_search=_env_bool("ENABLE_WEB_SEARCH", True),
            web_search_timeout_s=int(os.getenv("WEB_SEARCH_TIMEOUT_S", "8")),
            web_search_provider=(os.getenv("WEB_SEARCH_PROVIDER", "tavily").strip().lower() or "tavily"),
            tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
            tavily_base_url=(os.getenv("TAVILY_BASE_URL", "https://api.tavily.com").strip() or "https://api.tavily.com"),
            tavily_search_depth=(os.getenv("TAVILY_SEARCH_DEPTH", "advanced").strip().lower() or "advanced"),
            tavily_extract_depth=(os.getenv("TAVILY_EXTRACT_DEPTH", "advanced").strip().lower() or "advanced"),
            tavily_topic=(os.getenv("TAVILY_TOPIC", "general").strip().lower() or "general"),
            enable_llm=enable_llm,
            llm_provider=llm_provider,
            llm_api_key=api_key,
            llm_base_url=llm_base_url,
            llm_api_style=(os.getenv("LLM_API_STYLE", "auto").strip().lower() or "auto"),
            llm_model=os.getenv("OPENAI_MODEL", "gpt-4.1").strip(),
            llm_summary_model=os.getenv("OPENAI_SUMMARY_MODEL", "gpt-4.1-mini").strip(),
            llm_temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.2")),
            embedding_model=os.getenv(
                "OPENAI_EMBEDDING_MODEL",
                os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
            ).strip(),
            embedding_fallback_models=fallback_models or ("text-embedding-3-small",),
            embedding_dimensions=int(
                os.getenv(
                    "OPENAI_EMBEDDING_DIMENSIONS",
                    os.getenv("EMBEDDING_DIMENSIONS", "1536"),
                )
            ),
            enable_vector_search=enable_vector_search,
            vector_backend=os.getenv("VECTOR_BACKEND", "qdrant").strip() or "qdrant",
            qdrant_mode=(os.getenv("QDRANT_MODE", "auto").strip().lower() or "auto"),
            qdrant_url=qdrant_url,
            qdrant_api_key=os.getenv("QDRANT_API_KEY", "").strip(),
            qdrant_collection=(
                os.getenv("QDRANT_KNOWLEDGE_COLLECTION", os.getenv("QDRANT_COLLECTION", "clawfix_knowledge")).strip()
            ),
            qdrant_cases_collection=os.getenv("QDRANT_CASES_COLLECTION", "clawfix_cases").strip(),
            qdrant_session_collection=os.getenv("QDRANT_SESSION_COLLECTION", "clawfix_session_memory").strip(),
            qdrant_local_path=qdrant_local,
            vector_top_k=int(os.getenv("VECTOR_TOP_K", "6")),
            lexical_top_k=int(os.getenv("LEXICAL_TOP_K", "12")),
            rerank_top_n=int(os.getenv("RERANK_TOP_N", "12")),
            max_context_chars=int(os.getenv("MAX_CONTEXT_CHARS", "24000")),
            max_reply_chars=int(os.getenv("MAX_REPLY_CHARS", "4800")),
            max_search_results=int(os.getenv("MAX_SEARCH_RESULTS", "5")),
            max_delivery_retries=int(os.getenv("MAX_DELIVERY_RETRIES", "4")),
            delivery_poll_interval_s=float(os.getenv("DELIVERY_POLL_INTERVAL_S", "2.0")),
            session_compact_threshold_chars=int(os.getenv("SESSION_COMPACT_THRESHOLD_CHARS", "16000")),
            feishu_app_id=os.getenv("FEISHU_APP_ID", "").strip(),
            feishu_app_secret=os.getenv("FEISHU_APP_SECRET", "").strip(),
            feishu_connection_mode=(os.getenv("FEISHU_CONNECTION_MODE", "websocket").strip().lower() or "websocket"),
        )

    @property
    def sessions_dir(self) -> Path:
        return self.workspace_dir / ".sessions"

    @property
    def delivery_dir(self) -> Path:
        return self.workspace_dir / ".delivery"

    @property
    def dead_letter_dir(self) -> Path:
        return self.delivery_dir / "dead-letter"

    @property
    def memory_dir(self) -> Path:
        return self.workspace_dir / "memory"

    @property
    def cases_dir(self) -> Path:
        return self.workspace_dir / "cases"

    @property
    def knowledge_dir(self) -> Path:
        return self.workspace_dir / "knowledge"

    @property
    def vector_store_dir(self) -> Path:
        return Path(self.qdrant_local_path).resolve()

    @property
    def index_dir(self) -> Path:
        return self.workspace_dir / ".index"

    @property
    def retrieval_db_path(self) -> Path:
        return self.index_dir / "retrieval.db"

    def ensure_directories(self) -> None:
        for path in (
            self.workspace_dir,
            self.public_dir,
            self.sessions_dir,
            self.delivery_dir,
            self.dead_letter_dir,
            self.memory_dir,
            self.memory_dir / "daily",
            self.cases_dir,
            self.knowledge_dir,
            self.index_dir,
            self.vector_store_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
