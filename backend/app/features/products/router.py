"""商品模块路由汇总；先挂载列表静态路径，再挂载商品详情。"""
from fastapi import APIRouter

from .catalog.router import router as catalog_router
from .details.router import router as details_router
from .favorites.router import router as favorites_router
from .batch.router import router as batch_router

router = APIRouter()
router.include_router(catalog_router)
router.include_router(favorites_router)
router.include_router(batch_router)
router.include_router(details_router)
