"""T017: FastAPI 应用装配。"""
from __future__ import annotations

from fastapi import FastAPI

from app.config import settings
from app.faults.injector import FaultMiddleware
from app.observability.logging import configure_logging
from app.observability.trace import TraceMiddleware


def create_app() -> FastAPI:
    configure_logging(settings.log_level)

    app = FastAPI(title="Aftersales Mock Service", version="0.1.0")

    # 中间件顺序：后注册的先执行，因此 Trace 必须在 Fault 之后注册
    app.add_middleware(FaultMiddleware)
    app.add_middleware(TraceMiddleware)

    from app.api.routes import router

    app.include_router(router)

    return app


app = create_app()
