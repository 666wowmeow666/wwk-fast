import json
import logging
import unittest

from wwk.crm.client import (FourlogistClient, HttpResponse, TransportError, TransportTimeout, build_form)
from wwk.crm.errors import (CrmAuthError, CrmBadResponse, CrmForbiddenOperation, CrmNotFound, CrmTimeout,
                            CrmUnavailable, CrmWriteOutcomeUnknown)
from wwk.crm.mappers import describe_shape

TOKEN = "SECRET-TOKEN-abc123456"


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, *, params, form, headers, timeout):
        self.calls.append(dict(method=method, url=url, params=dict(params), form=list(form), headers=dict(headers), timeout=timeout))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def ok(obj):
    return HttpResponse(200, json.dumps(obj).encode(), {"content-type": "application/json"})


async def nosleep(_):
    return None


def client(transport, **kw):
    return FourlogistClient("https://crm.example/", TOKEN, transport=transport, sleep=nosleep, **kw)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_shape_and_auth_header(self):
        t = FakeTransport(ok({"items": [{"id": 1, "orderCode": "A-1", "clientId": 7}]}))
        order = await client(t).find_order_by_number("A-1")
        c = t.calls[0]
        self.assertEqual((c["method"], c["url"]), ("POST", "https://crm.example/api/orders/list"))
        self.assertEqual(c["form"], [("form_order_api_filter[orderCode]", "A-1")])
        self.assertEqual(c["params"], {"limit": "10"})
        self.assertEqual(c["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(c["timeout"], 15.0)
        self.assertEqual((order.order_id, order.client_id), ("1", "7"))

    async def test_auth_header_is_configurable(self):
        t = FakeTransport(ok([]))
        await client(t, auth_header="X-Api-Key", auth_scheme="").list_users()
        self.assertEqual(t.calls[0]["headers"]["X-Api-Key"], TOKEN)

    async def test_order_number_must_match_exactly(self):
        t = FakeTransport(ok([{"id": 1, "orderCode": "A-10", "clientId": 7}]))
        self.assertIsNone(await client(t).find_order_by_number("A-1"))   # фильтр CRM мог быть LIKE

    async def test_ambiguous_order_is_none(self):
        t = FakeTransport(ok([{"id": 1, "orderCode": "A-1", "clientId": 7}, {"id": 2, "orderCode": "a-1", "clientId": 8}]))
        self.assertIsNone(await client(t).find_order_by_number("A-1"))

    async def test_safe_read_retries_on_503_then_succeeds(self):
        t = FakeTransport(HttpResponse(503), TransportTimeout(), ok([]))
        await client(t, max_read_retries=2).list_users()
        self.assertEqual(len(t.calls), 3)

    async def test_safe_read_gives_up(self):
        t = FakeTransport(HttpResponse(503), HttpResponse(503), HttpResponse(503))
        with self.assertRaises(CrmUnavailable):
            await client(t, max_read_retries=2).list_users()
        self.assertEqual(len(t.calls), 3)

    async def test_timeout_maps_to_crm_timeout(self):
        t = FakeTransport(TransportTimeout())
        with self.assertRaises(CrmTimeout):
            await client(t, max_read_retries=0).list_users()

    async def test_500_not_retried_for_reads(self):
        t = FakeTransport(HttpResponse(500), ok([]))
        with self.assertRaises(CrmUnavailable):
            await client(t).list_users()
        self.assertEqual(len(t.calls), 1)

    async def test_status_mapping(self):
        for status, exc in ((401, CrmAuthError), (403, CrmAuthError), (404, CrmNotFound)):
            with self.subTest(status):
                with self.assertRaises(exc):
                    await client(FakeTransport(HttpResponse(status))).list_users()

    async def test_non_json_is_bad_response(self):
        with self.assertRaises(CrmBadResponse):
            await client(FakeTransport(HttpResponse(200, b"<html>"))).list_users()

    async def test_unexpected_structure_fails_closed(self):
        with self.assertRaises(CrmBadResponse):
            await client(FakeTransport(ok({"items": [{"foo": 1}]}))).find_order_by_number("A-1")

    async def test_writes_disabled_by_default(self):
        t = FakeTransport(ok({}))
        with self.assertRaises(CrmForbiddenOperation):
            await client(t).call("comments_new", form={"form[text]": "x"})
        self.assertEqual(t.calls, [])

    async def test_write_never_retried_and_unknown_outcome(self):
        for failure in (TransportTimeout(), TransportError("x"), HttpResponse(503)):
            with self.subTest(type(failure).__name__):
                t = FakeTransport(failure, ok({}))
                with self.assertRaises(CrmWriteOutcomeUnknown):
                    await client(t, allow_writes=True, max_read_retries=3).call("comments_new", form={"form[text]": "x"})
                self.assertEqual(len(t.calls), 1)

    async def test_blocked_get_endpoints_refused_even_with_writes_enabled(self):
        t = FakeTransport(ok({}))
        for key, pp in (("invoices_send", {"invoice": 1}), ("warehouse_handle_arrival", {"trip": 1})):
            with self.assertRaises(CrmForbiddenOperation):
                await client(t, allow_writes=True).call(key, path_params=pp)
        self.assertEqual(t.calls, [])

    async def test_unknown_key_refused(self):
        with self.assertRaises(CrmForbiddenOperation):
            await client(FakeTransport()).call("/api/anything")

    async def test_path_params_are_quoted(self):
        t = FakeTransport(ok({"id": 1, "orderCode": "X", "clientId": 2}))
        with self.assertRaises(Exception):
            await client(t).get_order("1/../../admin")   # all-info вернёт «другой заказ»/ошибку - но путь экранирован
        self.assertIn("1%2F..%2F..%2Fadmin", t.calls[0]["url"])

    async def test_token_never_logged_or_in_repr(self):
        records = []

        class H(logging.Handler):
            def emit(self, r):
                records.append(r.getMessage())

        lg = logging.getLogger("wwk.crm")
        h = H()
        lg.addHandler(h)
        lg.setLevel(logging.DEBUG)
        try:
            c = client(FakeTransport(ok([]), HttpResponse(401)))
            await c.list_users()
            with self.assertRaises(CrmAuthError) as cm:
                await c.list_users()
        finally:
            lg.removeHandler(h)
        self.assertTrue(records)
        self.assertTrue(all(TOKEN not in m for m in records))
        self.assertNotIn(TOKEN, repr(c))
        self.assertNotIn(TOKEN, str(cm.exception))

    async def test_probe_only_read_lists(self):
        c = client(FakeTransport(ok({"items": []})))
        self.assertEqual(await c.probe("orders_list"), {"items": []})
        with self.assertRaises(CrmForbiddenOperation):
            await c.probe("comments_new")

    async def test_order_all_info_mismatch_rejected(self):
        t = FakeTransport(ok({"id": 999, "orderCode": "A", "clientId": 1}))
        with self.assertRaises(CrmBadResponse):
            await client(t).get_order("5")


class FormTests(unittest.TestCase):
    def test_build_form(self):
        self.assertEqual(build_form({"a": 1, "b": None, "c": "", "d": [1, 2]}), [("a", "1"), ("d[]", "1"), ("d[]", "2")])

    def test_describe_shape_has_no_values(self):
        s = describe_shape({"id": 5, "name": "Секрет", "items": [{"inn": "7700000001"}]})
        self.assertEqual(s, {"id": "int", "name": "str", "items": [{"inn": "str"}]})
        self.assertNotIn("Секрет", json.dumps(s, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
