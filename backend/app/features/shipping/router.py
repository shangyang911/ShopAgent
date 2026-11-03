"""汇总发货物流子模块；各子路由保留原有 URL 和鉴权。"""
from .delivery.router import router as delivery_router
from .fulfillment.batch_router import router as batch_router
from .fulfillment.router import router as fulfillment_router
from .labels.router import router as labels_router
from .parcels.freight_router import router as freight_router
from .parcels.router import router as parcels_router
from .tracking.router import router as tracking_router

from fastapi import APIRouter


router = APIRouter()
router.include_router(fulfillment_router)
router.include_router(delivery_router)
router.include_router(tracking_router)
router.include_router(labels_router)
router.include_router(parcels_router)
router.include_router(batch_router)
router.include_router(freight_router)
