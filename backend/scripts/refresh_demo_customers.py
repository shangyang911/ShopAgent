"""Replace marked demo customer records, preserving IDs and historical orders.

Run from backend: .venv/Scripts/python.exe scripts/refresh_demo_customers.py
An exact backup is saved before changes; repeating a run leaves refreshed rows alone.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.features.customers.models import CustomerAddressModel, CustomerModel
from app.features.orders.models import OrderModel
from demo_customer_profiles import DEMO_CUSTOMERS, address_values


def refresh():
    with SessionLocal() as db:
        all_customers = list(db.scalars(select(CustomerModel).order_by(CustomerModel.id)))
        customers = [row for row in all_customers
                     if (row.email or "").endswith("@example.invalid")
                     or row.name.startswith(("路线规划测试客户-", "路线规划验收样例-"))]
        if not customers:
            return {"customers": 0, "addresses": 0, "already_updated": True}
        used_emails = {row.email for row in all_customers if row not in customers}
        profiles = [profile for profile in DEMO_CUSTOMERS if profile["email"] not in used_emails]
        if len(customers) > len(profiles):
            raise RuntimeError("More demo customers than available unique profiles")
        ids = [row.id for row in customers]
        addresses = list(db.scalars(select(CustomerAddressModel).where(
            CustomerAddressModel.customer_id.in_(ids)
        ).order_by(CustomerAddressModel.id)))
        def snapshot(row):
            return {column.name: getattr(row, column.name) for column in row.__table__.columns}
        # Historical order address snapshots must remain unchanged.
        original_orders = [dict(row._mapping) for row in db.execute(
            select(OrderModel.__table__).order_by(OrderModel.id)
        )]
        backup = Path(__file__).resolve().parents[2] / ".local-test" / (
            "customers-before-refresh-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps({"customers": [snapshot(row) for row in customers],
                                      "addresses": [snapshot(row) for row in addresses]},
                                     ensure_ascii=False, indent=2), encoding="utf-8")
        for index, customer in enumerate(customers):
            profile = profiles[index]
            for field in ("name", "phone", "email"):
                setattr(customer, field, profile[field])
            customer_addresses = [row for row in addresses if row.customer_id == customer.id]
            for offset, address in enumerate(customer_addresses):
                values = address_values(profile)
                if offset:
                    values["detail"] = f"和平路 云创商务中心{offset + 2}层{offset + 201}室"
                for field, value in values.items():
                    setattr(address, field, value)
        db.flush()
        if original_orders != [dict(row._mapping) for row in db.execute(
            select(OrderModel.__table__).order_by(OrderModel.id)
        )]:
            raise RuntimeError("Historical orders changed unexpectedly")
        db.commit()
        return {"customers": len(customers), "addresses": len(addresses),
                "cities": len({profiles[i]["city"] for i in range(len(customers))}),
                "backup": str(backup), "historical_orders_unchanged": True}


if __name__ == "__main__":
    print(json.dumps(refresh(), ensure_ascii=True))
