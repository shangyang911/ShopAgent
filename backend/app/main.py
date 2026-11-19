"""[后端应用入口] 配置 FastAPI、跨域策略、数据库启动初始化和 /api 路由。"""
from contextlib import asynccontextmanager
import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.api.router import api_router
from app.db.session import engine, initialize_database
from app.features.products.storage import PRODUCT_IMAGE_DIR

UPLOAD_DIR = PRODUCT_IMAGE_DIR.parent
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动时注册表结构、种子商品和运营期初账；各初始化步骤自行判断是否已执行。
    initialize_database()
    async def compensate_alerts():
        # 每五分钟补偿库存告警；同步数据库查询放入线程，避免阻塞异步请求的事件循环。
        from app.db.session import SessionLocal
        from app.features.operations.inventory import reconcile_alerts
        def run():
            with SessionLocal() as db:
                reconcile_alerts(db)
                db.commit()
        while True:
            await asyncio.sleep(300)
            try:
                await asyncio.to_thread(run)
            except Exception:
                logging.exception("Stock alert compensation failed")
    async def maintain_logistics():
        from app.db.session import SessionLocal
        from app.features.shipping.tracking.service import poll_due_shipments
        from app.features.shipping.tracking.settings import TrackingSettings
        while True:
            try:
                settings = TrackingSettings.from_env()
                # 外部轮询仅在账号已配置且显式启用时运行；单运单至少间隔30分钟。
                await poll_due_shipments(SessionLocal, settings)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.exception("Logistics maintenance failed")
            await asyncio.sleep(60)
    tasks = [asyncio.create_task(compensate_alerts()), asyncio.create_task(maintain_logistics())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass

app = FastAPI(title="E-commerce API", version="0.1.0", lifespan=lifespan)

# [本地前后端联调 / CORS] 仅允许 Vite 本地开发地址携带 Cookie 访问后端。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [商品图片 / 静态文件] 让数据库保存的 /uploads/... 图片路径可由浏览器访问。
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.include_router(api_router)


@app.get("/api/health", tags=["health"])
def health_check() -> dict[str, str]:
    """部署检查入口：确认 FastAPI 进程可以响应 HTTP 请求。"""
    return {"status": "ok", "service": "e-commerce-api"}


@app.get("/api/ready", tags=["health"])
def readiness_check() -> JSONResponse:
    """确认现有数据库连接和只读查询正常，不公开数据库错误详情。"""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return JSONResponse(content={"status": "ready"})

