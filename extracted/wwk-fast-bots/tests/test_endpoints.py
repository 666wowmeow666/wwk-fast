import json
import pathlib
import unittest

from wwk.crm.endpoints import ENDPOINTS, Safety

DOC = json.loads((pathlib.Path(__file__).parent.parent / "docs" / "4logist_openapi.json").read_text())


class EndpointRegistryTests(unittest.TestCase):
    def test_every_endpoint_exists_in_openapi(self):
        for ep in ENDPOINTS.values():
            with self.subTest(ep.key):
                self.assertIn(ep.path, DOC["paths"], f"{ep.path} нет в OpenAPI")
                self.assertIn(ep.method.lower(), DOC["paths"][ep.path], f"{ep.method} {ep.path}: метод не совпадает")

    def test_mutating_gets_are_blocked(self):
        for key in ("invoices_send", "act_send", "warehouse_handle_arrival",
                    "warehouse_trip_handle_departure", "warehouse_trip_handle_arrival"):
            self.assertIs(ENDPOINTS[key].safety, Safety.BLOCKED)
            self.assertEqual(ENDPOINTS[key].method, "GET")

    def test_safe_reads_are_only_read_summaries(self):
        for ep in ENDPOINTS.values():
            if ep.safety is Safety.READ_SAFE:
                summary = DOC["paths"][ep.path][ep.method.lower()].get("summary", "") or ""
                with self.subTest(ep.key):
                    self.assertRegex(summary.strip().lower(), r"^(get|find)", f"{ep.key}: '{summary}' не похоже на чтение")
                    for bad in ("send", "change", "create", "set ", "delete", "update", "issue"):
                        self.assertNotIn(bad, summary.lower())

    def test_writes_are_marked(self):
        for key in ("comments_new", "requests_new", "orders_edit", "orders_update_additional_manager"):
            self.assertIs(ENDPOINTS[key].safety, Safety.WRITE)


if __name__ == "__main__":
    unittest.main()
