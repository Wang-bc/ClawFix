from __future__ import annotations

import uvicorn

from app.config.settings import Settings
from app.gateway.server import create_fastapi_app


def _browser_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return bind_host


def main() -> None:
    settings = Settings.from_env()
    app = create_fastapi_app(settings)
    application = app.state.application
    vector_state = application.search_engine.vector_state()

    print(f"ClawFix bind on http://{settings.host}:{settings.port}")
    print(f"Open in browser: http://{_browser_host(settings.host)}:{settings.port}")
    print(
        "LLM config: "
        f"provider={settings.llm_provider}, base_url={settings.llm_base_url}, api_style={settings.llm_api_style}"
    )
    print(
        "Vector search: "
        f"{'enabled' if settings.enable_vector_search else 'disabled'}"
        + f", mode={vector_state.get('backend_mode', 'unknown')}"
        + (
            f", qdrant_url={vector_state.get('qdrant_url', '')}"
            if vector_state.get("backend_mode") == "remote"
            else f", local_path={vector_state.get('local_path', '')}"
        )
    )
    feishu_state = application.feishu_long_connection.status()
    print(
        "Feishu channel: "
        f"mode={feishu_state.get('mode', 'unknown')}, "
        f"configured={'yes' if feishu_state.get('configured') else 'no'}, "
        f"status={feishu_state.get('status', 'unknown')}"
    )
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
