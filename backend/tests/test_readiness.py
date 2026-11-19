"""Readiness probes never initialize or write to the configured business database."""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from app.main import app


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)  # Deliberately do not start application lifespan.
        self.addCleanup(self.client.close)

    def test_ready_queries_an_available_database(self):
        database = create_engine("sqlite://", connect_args={"check_same_thread": False})
        self.addCleanup(database.dispose)
        with patch("app.db.session.engine.connect", side_effect=database.connect):
            response = self.client.get("/api/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready"})

    def test_database_connection_failure_is_unavailable_without_secret_details(self):
        with patch("app.db.session.engine.connect", side_effect=OperationalError("secret-host", {}, Exception("secret-password"))):
            response = self.client.get("/api/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})

    def test_query_failure_is_unavailable(self):
        database = create_engine("sqlite://", connect_args={"check_same_thread": False})
        self.addCleanup(database.dispose)
        connection = database.connect()
        connection.close()
        with patch("app.db.session.engine.connect", return_value=connection):
            response = self.client.get("/api/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "not_ready"})

    def test_health_remains_a_process_only_probe(self):
        with patch("app.db.session.engine.connect", side_effect=AssertionError("health must not query the database")):
            response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "e-commerce-api"})


if __name__ == "__main__":
    unittest.main()
