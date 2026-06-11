"""Tests for the extended health probes (health_routes.py).

Covers the happy path for GET /api/health/db. The core /api/health is
tested elsewhere; here we only care that the DB probe round-trips and
returns the dialect + latency envelope.
"""
from __future__ import annotations

import unittest

from ._helpers import make_test_app


class HealthDbRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.store, self.adapter, self.temp_dir = make_test_app()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_db_returns_ok_on_sqlite(self) -> None:
        response = self.client.get("/api/health/db")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn(body["dialect"], {"sqlite", "postgres"})
        self.assertIsInstance(body["latency_ms"], int)
        self.assertGreaterEqual(body["latency_ms"], 0)

    def test_health_db_no_auth_required(self) -> None:
        # No cookie, no signup — this is a public probe like /api/health.
        self.assertNotIn("inspira_session", self.client.cookies)
        response = self.client.get("/api/health/db")
        self.assertEqual(response.status_code, 200)

    def test_core_health_still_works(self) -> None:
        # Sanity — adding the router should not break /api/health.
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["service"], "planning-studio")


if __name__ == "__main__":
    unittest.main()
