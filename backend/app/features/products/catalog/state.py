"""商品经营状态与库存风险分开；兼容旧目录的库存式状态名称。"""
from sqlalchemy import select

from app.features.operations.inventory import default_threshold
from app.features.operations.models import InventoryRule

LEGACY_STATES = {"Low stock": "Active", "Out of stock": "Active"}
PRODUCT_STATES = ("Draft", "Active", "Inactive", "Archived")


def business_status(value):
    return LEGACY_STATES.get(value, value)


def attach_inventory_state(db, products):
    """整页一次读取规则，避免每张商品卡独立查询阈值。"""
    if not products:
        return products
    rules = {row.product_id: row for row in db.scalars(select(InventoryRule).where(InventoryRule.product_id.in_([p.id for p in products])))}
    default = default_threshold(db)
    for product in products:
        rule = rules.get(product.id)
        threshold = rule.threshold_override if rule and rule.threshold_override is not None else default
        monitored = business_status(product.status) == "Active" and (rule.monitor_enabled if rule else True)
        product.inventory_status = ("out_of_stock" if product.stock == 0 else "low_stock" if product.stock <= threshold else "normal") if monitored else "unmonitored"
    return products
