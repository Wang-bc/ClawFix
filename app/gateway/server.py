from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
import json
import queue
from pathlib import Path
import threading
from typing import Any, Callable, Iterator
from uuid import uuid4

from fastapi import Body, FastAPI, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.agents.manager import AgentManager
from app.channels.feishu import FeishuChannel
from app.channels.feishu_long_connection import FeishuLongConnection
from app.config.models import AgentRunRequest, ChatAttachment
from app.config.settings import Settings
from app.delivery.queue import DeliveryQueue
from app.delivery.sender import SenderRegistry
from app.gateway.bindings import BindingStore
from app.gateway.inbound_pipeline import InboundPipeline
from app.gateway.routing import Router
from app.llm.client import LLMClient
from app.memory.search import MarkdownSearchEngine
from app.memory.store import CaseStore, KnowledgeStore
from app.observability.logging import configure_logging
from app.prompt.bootstrap_loader import BootstrapLoader
from app.prompt.builder import PromptBuilder
from app.prompt.memory_recall import MemoryRecall
from app.prompt.skill_loader import SkillLoader
from app.runtime.agent_loop import AgentLoop
from app.runtime.command_queue import SessionCommandQueue
from app.runtime.diagnostic_engine import DiagnosticEngine
from app.runtime.lane_queue import LaneQueue
from app.runtime.sub_agents import SubAgentRunner
from app.sessions.compactor import SessionCompactor
from app.sessions.context_guard import ContextGuard
from app.sessions.session_store import SessionStore
from app.sessions.summarizer import SessionSummarizer
from app.tools.builtins.files import register_file_tools
from app.tools.builtins.memory import register_memory_tools
from app.tools.builtins.time import register_time_tools
from app.tools.builtins.web import WebToolClient, register_web_tools
from app.tools.dispatcher import ToolDispatcher
from app.tools.registry import ToolRegistry


JsonDict = dict[str, object]
EventWriter = Callable[[JsonDict], None]


class AssistantApplication:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_directories()
        self.logger = configure_logging()

        self.agent_manager = AgentManager(settings.workspace_dir)
        self.llm_client = LLMClient(settings)
        self.search_engine = MarkdownSearchEngine(settings.workspace_dir, settings=settings, llm_client=self.llm_client)
        self.case_store = CaseStore(settings.workspace_dir)
        self.knowledge_store = KnowledgeStore(settings.workspace_dir)
        self.session_store = SessionStore(settings.sessions_dir)
        self.context_guard = ContextGuard(
            max_chars=settings.max_context_chars,
            compact_threshold_chars=settings.session_compact_threshold_chars,
        )
        self.session_summarizer = SessionSummarizer(self.llm_client)
        self.compactor = SessionCompactor(self.session_store, self.context_guard, self.session_summarizer)

        self.bootstrap_loader = BootstrapLoader(settings.workspace_dir)
        self.skill_loader = SkillLoader(settings.workspace_dir)
        self.memory_recall = MemoryRecall(self.search_engine)
        self.prompt_builder = PromptBuilder(self.bootstrap_loader, self.memory_recall, self.skill_loader)

        self.tool_registry = ToolRegistry()
        register_memory_tools(self.tool_registry, self.search_engine)
        register_time_tools(self.tool_registry)
        register_file_tools(self.tool_registry, settings.workspace_dir)
        register_web_tools(
            self.tool_registry,
            WebToolClient(
                timeout_s=settings.web_search_timeout_s,
                provider=settings.web_search_provider,
                tavily_api_key=settings.tavily_api_key,
                tavily_base_url=settings.tavily_base_url,
                tavily_search_depth=settings.tavily_search_depth,
                tavily_extract_depth=settings.tavily_extract_depth,
                tavily_topic=settings.tavily_topic,
            ),
        )
        self.tool_dispatcher = ToolDispatcher(self.tool_registry)

        self.sub_agent_runner = SubAgentRunner(
            session_store=self.session_store,
            prompt_builder=self.prompt_builder,
            dispatcher=self.tool_dispatcher,
            llm_client=self.llm_client,
        )
        self.diagnostic_engine = DiagnosticEngine(
            llm_client=self.llm_client,
            prompt_builder=self.prompt_builder,
            sub_agent_runner=self.sub_agent_runner,
            enable_web_search=settings.enable_web_search,
        )
        self.agent_loop = AgentLoop(
            session_store=self.session_store,
            context_guard=self.context_guard,
            compactor=self.compactor,
            prompt_builder=self.prompt_builder,
            diagnostic_engine=self.diagnostic_engine,
            case_store=self.case_store,
            search_engine=self.search_engine,
        )

        self.command_queue = SessionCommandQueue()
        self.lane_queue = LaneQueue()
        self.inbound_pipeline = InboundPipeline()
        self.router = Router(BindingStore(self.agent_manager.default_agent_id))
        self.background_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="clawfix-bg")

        self.feishu_channel = FeishuChannel(settings, self.inbound_pipeline)
        self.feishu_long_connection = FeishuLongConnection(settings, self._ingest_feishu_payload)
        self.sender_registry = SenderRegistry()
        self.sender_registry.register("web", self._send_web)
        self.sender_registry.register("feishu", self.feishu_channel.send_text)
        self.delivery_queue = DeliveryQueue(settings, self.sender_registry)
        self.delivery_queue.start()

        self.search_engine.build_index()
        self.feishu_long_connection.start()

    def shutdown(self) -> None:
        self.feishu_long_connection.stop()
        self.background_executor.shutdown(wait=False, cancel_futures=True)
        self.delivery_queue.stop()

    def handle_health(self) -> JsonDict:
        return {
            "ok": True,
            "service": "clawfix",
            "time": datetime.now().isoformat(timespec="seconds"),
            "features": {
                "llm_enabled": self.llm_client.enabled,
                "vector_search_enabled": self.settings.enable_vector_search,
                "web_search_enabled": self.settings.enable_web_search,
                "web_search_provider": self.settings.web_search_provider,
            },
            "channels": {
                "feishu": self.feishu_long_connection.status(),
            },
            "vector_state": self.search_engine.vector_state(),
        }

    def handle_web_chat(
        self,
        payload: JsonDict,
        event_callback=None,
    ) -> JsonDict:
        prepared = self._prepare_web_chat(payload)
        inbound = prepared["inbound"]
        route = prepared["route"]
        combined = prepared["combined"]
        output = self._run_request(
            route["agent_id"],
            route["session_key"],
            combined,
            inbound,
            "web",
            event_callback=event_callback,
        )
        delivery = self.delivery_queue.enqueue_and_send(
            run_id=output["run_id"],
            channel="web",
            account_id=inbound["account_id"],
            peer_id=inbound["peer_id"],
            text=output["reply_text"],
            metadata={"session_key": route["session_key"]},
        )
        return {
            "ok": True,
            "session_key": route["session_key"],
            "route": route,
            "delivery": delivery,
            "run": output,
        }

    def handle_web_chat_stream(self, payload: JsonDict, writer: EventWriter) -> None:
        prepared = self._prepare_web_chat(payload)
        writer({"type": "meta", "session_key": prepared["route"]["session_key"], "route": prepared["route"]})
        stream_payload = dict(payload)
        stream_payload["session_key"] = prepared["route"]["session_key"]
        result = self.handle_web_chat(
            stream_payload,
            event_callback=lambda event: writer({"type": "event", "event": event}),
        )
        writer({"type": "result", **result})

    def handle_finalize(self, payload: JsonDict) -> JsonDict:
        session_key = str(payload.get("session_key", "")).strip()
        if not session_key:
            raise ValueError("session_key cannot be empty")

        agent_id = str(payload.get("agent_id", self.agent_manager.default_agent_id))
        messages = self.session_store.load_messages(agent_id, session_key)
        last_user = next((item for item in reversed(messages) if item.get("role") == "user"), None)
        last_result = next(
            (
                item.get("metadata", {}).get("diagnostic_result")
                for item in reversed(messages)
                if item.get("role") == "assistant" and item.get("metadata", {}).get("diagnostic_result")
            ),
            None,
        )
        if not last_result:
            raise ValueError("no diagnostic result available for finalize")

        last_user_text = str((last_user or {}).get("text", ""))
        title = str(payload.get("title", "")).strip() or self._make_title(last_user_text)
        phenomenon = str(payload.get("phenomenon", "")).strip() or last_user_text
        final_root_cause = str(payload.get("final_root_cause", "")).strip()
        actual_fix = str(payload.get("actual_fix", "")).strip()

        path = self.case_store.write_case(
            title=title,
            phenomenon=phenomenon,
            result=last_result,
            session_key=session_key,
            final_root_cause=final_root_cause,
            actual_fix=actual_fix,
            source=str(payload.get("source", "web")),
        )
        vector_sync = self.search_engine.sync_case(path)
        relative_path = path.relative_to(self.settings.workspace_dir).as_posix()
        self.logger.info("Case finalized path=%s vector_sync=%s", relative_path, vector_sync)
        self.session_store.append_message(
            agent_id=agent_id,
            session_key=session_key,
            role="system",
            text=f"Finalize case root_cause={final_root_cause or 'n/a'}; actual_fix={actual_fix or 'n/a'}",
            metadata={"case_path": relative_path},
        )
        return {
            "ok": True,
            "path": relative_path,
            "title": title,
            "vector_sync": vector_sync,
        }

    def handle_cases(self, limit: int = 20) -> JsonDict:
        return {"ok": True, "items": self.case_store.list_cases(limit=limit)}

    def handle_case_delete(self, payload: JsonDict) -> JsonDict:
        relative_path = str(payload.get("path", "")).strip()
        if not relative_path:
            raise ValueError("case path cannot be empty")

        target = self.case_store.resolve_case_path(relative_path)
        try:
            self.case_store.delete_case(relative_path)
        except FileNotFoundError as exc:
            raise ValueError(f"case document not found: {relative_path}") from exc
        vector_sync = self.search_engine.sync_case(target)
        normalized_path = target.relative_to(self.settings.workspace_dir).as_posix()
        self.logger.info("Case deleted path=%s vector_sync=%s", normalized_path, vector_sync)
        return {"ok": True, "path": normalized_path, "vector_sync": vector_sync}

    def handle_knowledge(self, limit: int = 50) -> JsonDict:
        return {"ok": True, "items": self.knowledge_store.list_documents(limit=limit)}

    def handle_knowledge_import(self, payload: JsonDict) -> JsonDict:
        content = str(payload.get("content", "")).strip()
        if not content:
            raise ValueError("knowledge content cannot be empty")

        original_name = Path(str(payload.get("filename", "")).strip()).name
        relative_path = str(payload.get("path", "")).strip()
        if not relative_path and original_name:
            relative_path = f"uploads/{original_name}"
        title = str(payload.get("title", "")).strip() or Path(relative_path or original_name or "knowledge").stem

        try:
            path = self.knowledge_store.import_document(
                content=content,
                title=title,
                relative_path=relative_path,
                tags=self._normalize_string_list(payload.get("tags"), limit=16),
                overwrite=bool(payload.get("overwrite", True)),
            )
        except FileExistsError as exc:
            raise ValueError(str(exc)) from exc

        vector_sync = self.search_engine.sync_knowledge(path)
        normalized_path = path.relative_to(self.settings.workspace_dir).as_posix()
        self.logger.info("Knowledge imported path=%s vector_sync=%s", normalized_path, vector_sync)
        return {
            "ok": True,
            "path": normalized_path,
            "title": title or path.stem,
            "vector_sync": vector_sync,
        }

    def handle_knowledge_delete(self, payload: JsonDict) -> JsonDict:
        relative_path = str(payload.get("path", "")).strip()
        if not relative_path:
            raise ValueError("knowledge path cannot be empty")

        target = self.knowledge_store.resolve_managed_path(relative_path)
        try:
            self.knowledge_store.delete_document(relative_path)
        except FileNotFoundError as exc:
            raise ValueError(f"knowledge document not found: {relative_path}") from exc

        vector_sync = self.search_engine.sync_knowledge(target)
        normalized_path = target.relative_to(self.settings.workspace_dir).as_posix()
        self.logger.info("Knowledge deleted path=%s vector_sync=%s", normalized_path, vector_sync)
        return {"ok": True, "path": normalized_path, "vector_sync": vector_sync}

    def handle_sessions(self, limit: int = 20) -> JsonDict:
        return {"ok": True, "items": self.session_store.list_sessions(limit=limit, main_only=True)}

    def handle_session_delete(self, payload: JsonDict) -> JsonDict:
        session_key = str(payload.get("session_key", "")).strip()
        if not session_key:
            raise ValueError("session_key cannot be empty")

        deleted = self.session_store.delete_session_group(session_key)
        vector_sync = self.search_engine.sync_session_memory(session_key, [])
        self.logger.info("Session deleted session_key=%s summary=%s vector_sync=%s", session_key, deleted, vector_sync)
        return {"ok": True, **deleted, "vector_sync": vector_sync}

    def handle_session_detail(
        self,
        session_key: str,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> JsonDict:
        normalized_key = session_key if agent_id else self.session_store.normalize_main_session_key(session_key)
        target_agent = agent_id or self.agent_manager.default_agent_id
        session = self.session_store.load_session(target_agent, normalized_key, limit=limit)
        return {"ok": True, "session_key": normalized_key, **session}

    def handle_feishu_events(self, payload: JsonDict) -> JsonDict:
        return self._ingest_feishu_payload(payload, "webhook")

    def _ingest_feishu_payload(self, payload: JsonDict, source: str) -> JsonDict:
        if "challenge" in payload:
            return {"challenge": payload["challenge"]}

        if "encrypt" in payload and "event" not in payload:
            self.logger.error("Encrypted Feishu event is not supported in basic mode source=%s", source)
            return {"ok": False, "ignored": True, "reason": "encrypted_event_not_supported"}

        inbound = self.feishu_channel.parse_event(payload)
        if not inbound:
            header = payload.get("header", {})
            event_type = header.get("event_type", "") if isinstance(header, dict) else ""
            self.logger.info(
                "Ignored Feishu event source=%s event_type=%s keys=%s",
                source,
                event_type,
                sorted(payload.keys()),
            )
            return {"ok": True, "ignored": True}

        route = self.router.route(inbound)
        self.logger.info(
            "Accepted Feishu event source=%s message_id=%s peer_id=%s session_key=%s",
            source,
            inbound["message_id"],
            inbound["peer_id"],
            route["session_key"],
        )
        self.background_executor.submit(self._process_feishu_event, inbound, route)
        return {
            "ok": True,
            "accepted": True,
            "source": source,
            "session_key": route["session_key"],
            "message_id": inbound["message_id"],
        }

    def _process_feishu_event(self, inbound: JsonDict, route: JsonDict) -> None:
        try:
            output = self._run_request(route["agent_id"], route["session_key"], inbound["text"], inbound, "feishu")
            delivery = self.delivery_queue.enqueue_and_send(
                run_id=output["run_id"],
                channel="feishu",
                account_id=inbound["account_id"],
                peer_id=inbound["peer_id"],
                text=output["reply_text"],
                metadata={"session_key": route["session_key"]},
            )
            self.logger.info(
                "Processed Feishu event message_id=%s run_id=%s delivery_status=%s",
                inbound["message_id"],
                output["run_id"],
                delivery.get("status", "unknown"),
            )
        except Exception:  # noqa: BLE001
            self.logger.exception(
                "Feishu event processing failed message_id=%s session_key=%s",
                inbound.get("message_id", ""),
                route.get("session_key", ""),
            )

    def _prepare_web_chat(self, payload: JsonDict) -> JsonDict:
        text = str(payload.get("text", "")).strip()
        attachments = payload.get("attachments", [])
        combined = self._combine_input(text, attachments)
        if not combined.strip():
            raise ValueError("request content cannot be empty")

        user_id = str(payload.get("user_id", "web-user"))
        session_key = self._resolve_web_session_key(self._optional_text(payload.get("session_key")), user_id)
        inbound = self.inbound_pipeline.build_inbound(
            channel="web",
            account_id="web-console",
            peer_id=user_id,
            sender_id=user_id,
            sender_name=str(payload.get("sender_name", "Web User")),
            text=combined,
            raw_payload=payload,
        )
        route = self.router.route(inbound, requested_session_key=session_key)
        return {"combined": combined, "inbound": inbound, "route": route}

    def _run_request(
        self,
        agent_id: str,
        session_key: str,
        user_text: str,
        inbound: JsonDict | None,
        source: str,
        event_callback=None,
    ) -> JsonDict:
        request = AgentRunRequest(
            run_id=uuid4().hex,
            agent_id=agent_id,
            session_key=session_key,
            user_text=user_text,
            inbound=inbound,
            source=source,
            created_at=datetime.now().isoformat(timespec="seconds"),
            timeout_s=60,
        )
        return self.command_queue.run(
            session_key,
            lambda: self.lane_queue.run("main", lambda: self.agent_loop.run(request, event_callback=event_callback)),
        )

    def _combine_input(self, text: str, attachments: object) -> str:
        blocks = [text.strip()] if text.strip() else []
        if isinstance(attachments, list):
            for item in attachments:
                if not isinstance(item, dict):
                    continue
                attachment: ChatAttachment = item  # type: ignore[assignment]
                name = str(attachment.get("name", "unnamed.txt"))
                content = str(attachment.get("content", "")).strip()
                if not content:
                    continue
                blocks.append(f"[Attachment {name}]\n{content}")
        return "\n\n".join(blocks).strip()

    def _make_title(self, text: str) -> str:
        text = " ".join(text.split())
        return text[:48] or "Untitled incident"

    def _optional_text(self, value: object) -> str | None:
        text = str(value or "").strip()
        return text or None

    def _normalize_string_list(self, value: object, limit: int = 8) -> list[str]:
        if not isinstance(value, list):
            return []
        items = [str(item).strip() for item in value if str(item).strip()]
        return items[:limit]

    def _resolve_web_session_key(self, requested_session_key: str | None, user_id: str) -> str:
        if requested_session_key:
            return self.session_store.normalize_main_session_key(requested_session_key)
        return f"{self.agent_manager.default_agent_id}:web:web-console:{user_id}:{uuid4().hex[:12]}"

    def _send_web(
        self,
        account_id: str,
        peer_id: str,
        text: str,
        metadata: JsonDict | None = None,
    ) -> JsonDict:
        _ = (account_id, peer_id, metadata)
        return {"sent": True, "channel": "web", "preview": text[:120]}


def create_fastapi_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    application = AssistantApplication(app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> Iterator[None]:
        try:
            yield
        finally:
            application.shutdown()

    app = FastAPI(title="ClawFix", lifespan=lifespan)
    app.state.application = application
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def json_response(payload: JsonDict, status_code: int = 200) -> JSONResponse:
        return JSONResponse(content=jsonable_encoder(payload), status_code=status_code)

    def run_json(handler: Callable[[], JsonDict], *, context: str) -> JSONResponse:
        try:
            return json_response(handler())
        except ValueError as exc:
            return json_response({"ok": False, "error": str(exc)}, status_code=400)
        except FileNotFoundError:
            return json_response({"ok": False, "error": "Not Found"}, status_code=404)
        except Exception as exc:  # noqa: BLE001
            application.logger.exception("%s failed", context)
            return json_response({"ok": False, "error": str(exc)}, status_code=500)

    def stream_ndjson(payload: JsonDict) -> StreamingResponse:
        messages: "queue.Queue[bytes | None]" = queue.Queue()

        def writer(item: JsonDict) -> None:
            data = (json.dumps(item, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            messages.put(data)

        def worker() -> None:
            try:
                application.handle_web_chat_stream(payload, writer)
            except Exception as exc:  # noqa: BLE001
                application.logger.exception("POST /api/web/chat/stream failed")
                writer({"type": "error", "error": str(exc)})
            finally:
                messages.put(None)

        def iterator() -> Iterator[bytes]:
            while True:
                chunk = messages.get()
                if chunk is None:
                    break
                yield chunk

        threading.Thread(target=worker, daemon=True, name="clawfix-stream").start()
        return StreamingResponse(
            iterator(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/health")
    def health() -> JSONResponse:
        return run_json(application.handle_health, context="GET /api/health")

    @app.get("/api/cases")
    def cases(limit: int = Query(default=20, ge=1, le=200)) -> JSONResponse:
        return run_json(lambda: application.handle_cases(limit=limit), context="GET /api/cases")

    @app.get("/api/knowledge")
    def knowledge(limit: int = Query(default=50, ge=1, le=200)) -> JSONResponse:
        return run_json(lambda: application.handle_knowledge(limit=limit), context="GET /api/knowledge")

    @app.get("/api/sessions")
    def sessions(limit: int = Query(default=20, ge=1, le=200)) -> JSONResponse:
        return run_json(lambda: application.handle_sessions(limit=limit), context="GET /api/sessions")

    @app.get("/api/session")
    def session_detail(
        session_key: str = Query(..., min_length=1),
        limit: int = Query(default=100, ge=1, le=500),
        agent_id: str | None = Query(default=None),
    ) -> JSONResponse:
        return run_json(
            lambda: application.handle_session_detail(session_key, agent_id=agent_id, limit=limit),
            context="GET /api/session",
        )

    @app.post("/api/web/chat")
    def web_chat(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        body = payload or {}
        return run_json(lambda: application.handle_web_chat(body), context="POST /api/web/chat")

    @app.post("/api/web/chat/stream")
    def web_chat_stream(payload: dict[str, Any] | None = Body(default=None)) -> StreamingResponse:
        return stream_ndjson(payload or {})

    @app.post("/api/web/finalize")
    def finalize(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        body = payload or {}
        return run_json(lambda: application.handle_finalize(body), context="POST /api/web/finalize")

    @app.post("/api/cases/delete")
    def case_delete(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        body = payload or {}
        return run_json(lambda: application.handle_case_delete(body), context="POST /api/cases/delete")

    @app.post("/api/knowledge/import")
    def knowledge_import(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        body = payload or {}
        return run_json(lambda: application.handle_knowledge_import(body), context="POST /api/knowledge/import")

    @app.post("/api/knowledge/delete")
    def knowledge_delete(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        body = payload or {}
        return run_json(lambda: application.handle_knowledge_delete(body), context="POST /api/knowledge/delete")

    @app.post("/api/sessions/delete")
    def session_delete(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        body = payload or {}
        return run_json(lambda: application.handle_session_delete(body), context="POST /api/sessions/delete")

    @app.post("/api/feishu/events")
    def feishu_events(payload: dict[str, Any] | None = Body(default=None)) -> JSONResponse:
        body = payload or {}
        return run_json(lambda: application.handle_feishu_events(body), context="POST /api/feishu/events")

    app.mount("/", StaticFiles(directory=str(app_settings.public_dir), html=True), name="static")
    return app
