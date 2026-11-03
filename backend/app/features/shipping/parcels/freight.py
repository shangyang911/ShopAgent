"""以交接时间为统计口径；收入按订单计一次，实际支出按包裹计。"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import or_, select

from app.features.operations.common import wire
from app.features.orders.models import OrderModel
from app.features.shipping.fulfillment.models import ShipmentModel
from app.features.shipping.parcels.models import ExtraParcel, FreightRecord


def freight_report(db, start_at=None, end_at=None, carrier=None):
    """按交接时间选单，按包裹汇总已登记支出；缺少运费表示未结算，不能当成免费。"""
    statement = select(OrderModel, ShipmentModel).join(ShipmentModel, ShipmentModel.order_id == OrderModel.id).where(
        or_(ShipmentModel.dispatched_at.is_not(None), ShipmentModel.historical == True))
    if start_at:
        statement = statement.where(ShipmentModel.dispatched_at >= start_at)
    if end_at:
        statement = statement.where(ShipmentModel.dispatched_at < end_at)
    orders = list(db.execute(statement))
    ids = [order.id for order, _ in orders]
    extras = defaultdict(list)
    costs = {}
    if ids:
        for row in db.scalars(select(ExtraParcel).where(ExtraParcel.order_id.in_(ids))):
            extras[row.order_id].append(row)
        costs = {(row.order_id, row.package_no): row.actual_cost for row in db.scalars(select(FreightRecord).where(FreightRecord.order_id.in_(ids)))}
    groups, details = {}, []
    order_count, fee_income, actual_total, missing = 0, Decimal('0.00'), Decimal('0.00'), 0
    for order, first in orders:
        rows = [(1, first)] + [(row.package_no, row) for row in extras[order.id]]
        if carrier:
            from app.features.shipping.parcels.service import carrier_identity
            rows = [(no, row) for no, row in rows if carrier_identity(row.carrier_name) == carrier_identity(carrier)]
        if not rows:
            continue
        order_count += 1
        # 一单拆成多个包裹时，客户支付的运费仍只计一次；下方循环才累计各包裹实际支出。
        fee_income += order.shipping_fee
        for no, row in rows:
            from app.features.shipping.parcels.service import carrier_identity
            code = carrier_identity(row.carrier_name)
            group = groups.setdefault(code, {"carrier": code, "carrier_name": row.carrier_name, "packages": 0, "settled_packages": 0, "unsettled_packages": 0, "actual_cost": Decimal('0.00')})
            group["packages"] += 1
            cost = costs.get((order.id, no))
            if cost is None:
                missing += 1
                group["unsettled_packages"] += 1
            else:
                actual_total += cost
                group["settled_packages"] += 1
                group["actual_cost"] += cost
            details.append({"order_id": order.id, "order_number": order.order_number, "package_no": no,
                            "carrier_name": row.carrier_name, "tracking_number": row.tracking_number,
                            "dispatched_at": first.dispatched_at, "historical": first.historical, "actual_cost": cost})
    return wire({"order_count": order_count, "package_count": len(details), "settled_packages": len(details) - missing,
                 "unsettled_packages": missing, "actual_cost": actual_total, "customer_shipping_fee": fee_income,
                 "complete": missing == 0, "groups": sorted(groups.values(), key=lambda row: row['carrier']), "items": details,
                 "time_basis": "dispatched_at", "income_scope": "匹配包裹所属订单的客户运费，每单仅计一次"})
