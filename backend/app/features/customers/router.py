"""客户和收货地址 HTTP 接口。"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.features.auth.router import require_current_user
from app.features.customers.crud import CustomerCRUD
from app.features.customers.models import CustomerModel
from app.features.customers.schemas import AddressCreate, AddressUpdate, Customer, CustomerAddress, CustomerCreate, CustomerUpdate

router = APIRouter(prefix="", tags=["customers"], dependencies=[Depends(require_current_user)])


@router.get("/customers", response_model=list[Customer])
def list_customers(db: Session = Depends(get_db)):
    return CustomerCRUD(db).list_customers()


@router.post("/customers", response_model=Customer, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, db: Session = Depends(get_db)):
    return CustomerCRUD(db).create_customer(payload.model_dump())


@router.put("/customers/{customer_id}", response_model=Customer)
def update_customer(customer_id: int, payload: CustomerUpdate, db: Session = Depends(get_db)):
    row = CustomerCRUD(db).update_customer(customer_id, payload.model_dump())
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


@router.get("/customers/{customer_id}/addresses", response_model=list[CustomerAddress])
def list_addresses(customer_id: int, db: Session = Depends(get_db)):
    crud = CustomerCRUD(db)
    if db.get(CustomerModel, customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return crud.list_addresses(customer_id)


@router.post("/addresses", response_model=CustomerAddress, status_code=status.HTTP_201_CREATED)
def create_address(payload: AddressCreate, db: Session = Depends(get_db)):
    row = CustomerCRUD(db).create_address(payload.model_dump())
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


@router.put("/addresses/{address_id}", response_model=CustomerAddress)
def update_address(address_id: int, payload: AddressUpdate, db: Session = Depends(get_db)):
    row = CustomerCRUD(db).update_address(address_id, payload.model_dump())
    if row is None:
        raise HTTPException(status_code=404, detail="Address not found")
    return row


@router.delete("/addresses/{address_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_address(address_id: int, db: Session = Depends(get_db)):
    if not CustomerCRUD(db).delete_address(address_id):
        raise HTTPException(status_code=404, detail="Address not found")
