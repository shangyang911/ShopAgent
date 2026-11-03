"""[后端 API 路由入口] 汇总各功能模块路由并统一挂载到 /api 前缀。"""
from fastapi import APIRouter

from app.features.auth.router import router as auth_router
from app.features.customers.router import router as customers_router
from app.features.orders.router import router as orders_router
from app.features.products.router import router as products_router
from app.features.shipping.router import router as shipping_router
from app.features.operations.inventory_router import router as inventory_router
from app.features.operations.orders_router import router as order_operations_router
from app.features.operations.dashboard import router as dashboard_router
from app.features.agent.router import router as agent_router
from app.features.agent.observability import router as observability_router
from app.features.knowledge.router import router as knowledge_router
from app.features.aftersales.router import router as aftersales_router
from app.features.inventory.router import router as adjustments_router
from app.features.inventory.copilot_router import router as copilot_router
from app.features.notifications.router import router as notifications_router
from app.features.customers.details.router import router as customer_details_router

api_router = APIRouter(prefix="/api")
api_router.include_router(auth_router)
api_router.include_router(customers_router)
api_router.include_router(orders_router)
api_router.include_router(products_router)
api_router.include_router(shipping_router)
api_router.include_router(inventory_router)
api_router.include_router(order_operations_router)
api_router.include_router(dashboard_router)
api_router.include_router(agent_router)
api_router.include_router(observability_router)
api_router.include_router(knowledge_router)
api_router.include_router(aftersales_router)
api_router.include_router(adjustments_router)
api_router.include_router(copilot_router)
api_router.include_router(notifications_router)
api_router.include_router(customer_details_router)
