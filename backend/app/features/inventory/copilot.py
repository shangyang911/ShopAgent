# [运营建议] 后端计算可核对的SKU补货数量和待发货条件，AI只负责调用与说明。
"""Deterministic SKU planning. LLMs explain these facts; they never calculate inventory writes."""
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from sqlalchemy import func, select
from app.features.operations.common import digest
from app.features.operations.models import Inbound, InboundLine, OrderFacts, utcnow
from app.features.orders.models import OrderModel, OrderItemModel, OrderStatus
from app.features.products.constants import SELLABLE_STATUSES
from app.features.products.models import ProductModel, ProductSKU


def replenishment(db, keyword='', coverage_days=7, safety_percent=15, minimum_stock=5):
    now = utcnow()
    statement = select(ProductSKU, ProductModel.name).join(ProductModel, ProductModel.id == ProductSKU.product_id).where(
        ProductSKU.active.is_(True), ProductModel.status.in_(SELLABLE_STATUSES))
    if keyword.strip():
        statement = statement.where(ProductModel.name.contains(keyword.strip(), autoescape=True))
    sku_rows = db.execute(statement.order_by(ProductSKU.stock, ProductSKU.id).limit(501)).all()
    ids = [sku.id for sku, _ in sku_rows[:500]]
    demand = {}
    # 需求按可靠收款时间和有效履约状态统计；未付款、已取消订单不用于补货推算。
    for days in (7, 30):
        demand[days] = dict(db.execute(select(OrderItemModel.sku_id, func.sum(OrderItemModel.quantity)).join(
            OrderModel, OrderModel.id == OrderItemModel.order_id).join(OrderFacts, OrderFacts.order_id == OrderModel.id).where(
            OrderItemModel.sku_id.in_(ids), OrderFacts.paid_at >= now-timedelta(days=days), OrderFacts.paid_at <= now,
            OrderModel.status.in_([OrderStatus.PAID_PENDING_SHIPMENT, OrderStatus.IN_DELIVERY, OrderStatus.COMPLETED])
        ).group_by(OrderItemModel.sku_id)).all())
    draft = dict(db.execute(select(InboundLine.sku_id, func.sum(InboundLine.quantity)).join(
        Inbound, Inbound.id == InboundLine.receipt_id).where(Inbound.status == 'draft', InboundLine.sku_id.in_(ids)
        ).group_by(InboundLine.sku_id)).all())
    items = []
    # 草稿只用于避免重复计划，不当作确定到货库存；可售已经扣预留，不再重复扣减。
    for sku, name in sku_rows[:500]:
        sold7, sold30 = int(demand[7].get(sku.id, 0)), int(demand[30].get(sku.id, 0))
        daily = max(Decimal(sold7)/7, Decimal(sold30)/30)
        # 两个窗口取较高日均需求，加安全比例后向上取整，且不低于每SKU最低目标。
        target = max(minimum_stock, int((daily*coverage_days*(1+Decimal(safety_percent)/100)).to_integral_value(rounding=ROUND_CEILING)))
        planned = int(draft.get(sku.id, 0))
        row = {'product_id': sku.product_id, 'sku_id': sku.id, 'name': name, 'sku_code': sku.code,
               'label': ' / '.join(v for v in [sku.color, sku.size] if v) or '默认规格',
               'available': sku.stock, 'reserved': sku.reserved, 'paid_units_7d': sold7, 'paid_units_30d': sold30,
               'daily_demand': round(float(daily), 3), 'coverage_days': round(float(Decimal(sku.stock)/daily), 1) if daily else None,
               'target': target, 'draft_quantity': planned, 'suggested_quantity': max(0, target-sku.stock-planned),
               'confidence': '近期需求参考' if sold30 else '缺少需求历史，使用最低备货量'}
        row['basis'] = digest({**row, 'revision': sku.revision, 'parameters': [coverage_days, safety_percent, minimum_stock]})
        # 来源摘要随库存、需求、草稿或版本变化而改变，创建草稿时据此拒绝过期建议。
        items.append(row)
    items.sort(key=lambda r: (-r['suggested_quantity'], r['available'], r['sku_id']))
    return {'generated_at': now.isoformat()+'Z', 'parameters': {'keyword': keyword, 'coverage_days': coverage_days,
        'safety_percent': safety_percent, 'minimum_stock': minimum_stock}, 'items': items, 'truncated': len(sku_rows)>500,
        'notice': '规则建议，非销量预测。需求=近7/30天已收款且未取消订单件数（日均取较高值），含待发货需求，未按退款金额折算退货件数。可售已扣预留，不重复扣减。已有草稿仅用于避免重复计划，不代表采购在途或确定到货；覆盖天数仅按当前可售计算。',
        'formula': '目标=max(最低备货量, ceil(日均需求×备货天数×(1+安全比例)))；新建建议=max(0,目标−可售−已有草稿)'}


def shipping_analysis(db):
    # 只陈述当前队列的核对条件；没有历史快照时，不推断积压增长的真实原因。
    from app.features.aftersales.models import AfterSale
    from app.features.shipping.fulfillment.models import ShipmentModel
    from app.features.shipping.labels.models import LabelTask
    now = utcnow()
    orders = db.scalars(select(OrderModel).where(OrderModel.status == OrderStatus.PAID_PENDING_SHIPMENT).order_by(OrderModel.id).limit(501)).all()
    ids = [o.id for o in orders[:500]]
    shipments = {s.order_id: s for s in db.scalars(select(ShipmentModel).where(ShipmentModel.order_id.in_(ids)))}
    aftersales = set(db.scalars(select(AfterSale.order_id).where(AfterSale.order_id.in_(ids), AfterSale.status == 'open')))
    labels = {}
    for task in db.scalars(select(LabelTask).where(LabelTask.order_id.in_(ids)).order_by(LabelTask.id)):
        labels[(task.order_id, task.package_no)] = task.status
    facts = {f.order_id: f for f in db.scalars(select(OrderFacts).where(OrderFacts.order_id.in_(ids)))}
    items = []
    for order in orders[:500]:
        reasons = []
        if order.id not in shipments:
            reasons.append('尚未登记主运单')
        if order.id in aftersales:
            reasons.append('处理中售后阻止交接')
        statuses = [status for (oid, _), status in labels.items() if oid == order.id]
        if any(s in ('requesting', 'unknown') for s in statuses):
            reasons.append('面单申请中或结果待核实')
        if 'failed' in statuses:
            reasons.append('最近面单申请失败，需核对或使用纸质运单')
        paid = facts.get(order.id)
        hours = round((now-paid.paid_at).total_seconds()/3600, 1) if paid and paid.paid_at else None
        items.append({'order_id': order.id, 'order_number': order.order_number, 'waiting_hours': hours,
                      'overdue': hours is not None and hours >= 24, 'reasons': reasons or ['已登记运单，待仓库核实实物交接'],
                      'href': f'/shipping?order_id={order.id}'})
    return {'generated_at': now.isoformat()+'Z', 'items': items, 'truncated': len(orders)>500,
            'notice': '仅分析当前已付款待发货队列；原因标签可重叠，不等同于真实延误根因。无完整历史队列快照，不能据此断言今日积压增长原因。'}
