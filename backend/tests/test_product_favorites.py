"""收藏真实 API 回归：鉴权、账号隔离、幂等和商品生命周期。"""
import unittest

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import test_core_operations as fixtures
from app.features.auth.models import UserModel
from app.features.auth.router import require_current_user
from app.features.products.catalog.crud import ProductCRUD
from app.features.products.favorites.models import ProductFavorite
from app.features.products.models import ProductModel
from app.main import app


class ProductFavoriteTests(unittest.TestCase):
    setUp = fixtures.CoreOperationsTests.setUp
    tearDown = fixtures.CoreOperationsTests.tearDown

    def product(self, name="收藏测试商品"):
        with Session(self.engine, expire_on_commit=False) as db:
            row = ProductModel(name=name, category="Test", price=10, stock=5, status="Active",
                               image_url="/test.png", color="red", shipping="Standard")
            db.add(row)
            db.commit()
            return row.id

    def test_save_reload_and_remove_are_idempotent(self):
        pid = self.product()
        path = f"/api/products/favorites/{pid}"
        self.assertEqual(self.client.get("/api/products/favorites").json(), [])
        for _ in range(2):
            self.assertEqual(self.client.put(path).status_code, 204)
        rows = self.client.get("/api/products/favorites")
        self.assertEqual(rows.status_code, 200)
        self.assertEqual([row["id"] for row in rows.json()], [pid])
        self.assertEqual(rows.json()[0]["price"], 10)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(ProductFavorite)), 1)
        for _ in range(2):
            self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.get("/api/products/favorites").json(), [])

    def test_favorites_belong_to_current_account(self):
        pid = self.product()
        path = f"/api/products/favorites/{pid}"
        self.client.put(path)
        with Session(self.engine, expire_on_commit=False) as db:
            other = UserModel(username="other-admin", password_hash="unused")
            db.add(other)
            db.commit()
        app.dependency_overrides[require_current_user] = lambda: other
        self.assertEqual(self.client.get("/api/products/favorites").json(), [])
        self.assertEqual(self.client.delete(path).status_code, 204)
        app.dependency_overrides[require_current_user] = lambda: self.actor
        self.assertEqual(len(self.client.get("/api/products/favorites").json()), 1)

    def test_current_product_values_and_deleted_product_cleanup(self):
        pid = self.product()
        self.client.put(f"/api/products/favorites/{pid}")
        with Session(self.engine) as db:
            product = db.get(ProductModel, pid)
            product.name, product.price, product.stock = "最新名称", 25, 3
            db.commit()
        row = self.client.get("/api/products/favorites").json()[0]
        self.assertEqual((row["name"], row["price"], row["stock"]), ("最新名称", 25, 3))
        with Session(self.engine) as db:
            self.assertTrue(ProductCRUD(db).delete_product(pid))
            self.assertEqual(db.scalar(select(func.count()).select_from(ProductFavorite)), 0)
        self.assertEqual(self.client.get("/api/products/favorites").json(), [])

    def test_missing_product_cannot_be_favorited(self):
        self.assertEqual(self.client.put("/api/products/favorites/999999").status_code, 404)
        self.assertEqual(self.client.get("/api/products/favorites").json(), [])

    def test_all_favorite_endpoints_require_login(self):
        app.dependency_overrides.pop(require_current_user)
        for method, path in [("GET", "/api/products/favorites"),
                             ("PUT", "/api/products/favorites/1"),
                             ("DELETE", "/api/products/favorites/1")]:
            self.assertEqual(self.client.request(method, path).status_code, 401)
