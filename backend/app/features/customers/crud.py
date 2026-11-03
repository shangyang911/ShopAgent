"""客户与地址数据库操作。"""
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.features.customers.models import CustomerAddressModel, CustomerModel


class CustomerCRUD:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_customers(self) -> list[CustomerModel]:
        return list(self.db.scalars(select(CustomerModel).order_by(CustomerModel.id.desc())).all())

    def create_customer(self, values: dict) -> CustomerModel:
        row = CustomerModel(**values)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_customer(self, customer_id: int, values: dict) -> CustomerModel | None:
        row = self.db.get(CustomerModel, customer_id)
        if row is None:
            return None
        for key, value in values.items():
            setattr(row, key, value)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list_addresses(self, customer_id: int) -> list[CustomerAddressModel]:
        return list(self.db.scalars(select(CustomerAddressModel).where(CustomerAddressModel.customer_id == customer_id).order_by(CustomerAddressModel.is_default.desc(), CustomerAddressModel.id.desc())).all())

    def create_address(self, values: dict) -> CustomerAddressModel | None:
        if self.db.get(CustomerModel, values["customer_id"]) is None:
            return None
        if values.get("is_default"):
            self.db.execute(update(CustomerAddressModel).where(CustomerAddressModel.customer_id == values["customer_id"]).values(is_default=False))
        row = CustomerAddressModel(**values)
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def update_address(self, address_id: int, values: dict) -> CustomerAddressModel | None:
        row = self.db.get(CustomerAddressModel, address_id)
        if row is None:
            return None
        if values.get("is_default"):
            self.db.execute(update(CustomerAddressModel).where(CustomerAddressModel.customer_id == row.customer_id).values(is_default=False))
        for key, value in values.items():
            setattr(row, key, value)
        self.db.commit()
        self.db.refresh(row)
        return row

    def delete_address(self, address_id: int) -> bool:
        row = self.db.get(CustomerAddressModel, address_id)
        if row is None:
            return False
        self.db.delete(row)
        self.db.commit()
        return True
