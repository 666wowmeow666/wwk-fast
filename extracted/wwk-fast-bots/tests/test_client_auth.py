import dataclasses
import unittest

from tests.helpers import make_services
from wwk.core.client_auth import (ClientAuthPolicy, DocumentTooLarge, VerifyStatus, normalize_order_number)
from wwk.core.employees import AccessDenied
from wwk.core.models import BindingStatus, Role
from wwk.crm.errors import CrmUnavailable
from wwk.crm.mock import MockFourlogistAdapter

A = ("WWK-0001", "0001")   # клиент 1001, ИНН ...0001
B = ("WWK-0002", "0002")   # клиент 1002, ИНН ...0002


class Base(unittest.IsolatedAsyncioTestCase):
    policy = ClientAuthPolicy(visible_doc_groups=frozenset({"cmr"}))

    def setUp(self):
        self.s = make_services(self.policy)


class VerifyTests(Base):
    async def test_success_creates_binding_and_is_remembered(self):
        r = await self.s.client_auth.verify(1, *A)
        self.assertEqual(r.status, VerifyStatus.OK)
        self.assertEqual(r.binding.org_key, "1001")
        again = await self.s.client_auth.verify(1, *A)
        self.assertEqual(again.binding.id, r.binding.id)
        self.assertEqual(len(await self.s.client_auth.list_valid_bindings(1)), 1)

    async def test_wrong_inn_and_unknown_order_are_indistinguishable(self):
        wrong_inn = await self.s.client_auth.verify(1, "WWK-0001", "9999")
        no_order = await self.s.client_auth.verify(1, "WWK-4040", "0001")
        self.assertEqual(wrong_inn.status, VerifyStatus.INVALID)
        self.assertEqual(wrong_inn, no_order)
        self.assertEqual(await self.s.client_auth.list_valid_bindings(1), [])

    async def test_bad_input_not_sent_to_crm(self):
        for o, i in (("", "0001"), ("WWK-0001", "12"), ("WWK-0001", "abcd"), ("WWK-0001", "00001"), ("a" * 100, "0001"), ("<script>", "0001")):
            r = await self.s.client_auth.verify(1, o, i)
            self.assertEqual(r.status, VerifyStatus.BAD_INPUT, (o, i))

    async def test_lockout_per_telegram(self):
        for _ in range(5):
            self.assertEqual((await self.s.client_auth.verify(1, "WWK-0001", "9999")).status, VerifyStatus.INVALID)
        self.assertEqual((await self.s.client_auth.verify(1, *A)).status, VerifyStatus.LOCKED)   # даже верные данные
        self.s.clock.advance(minutes=31)
        self.assertEqual((await self.s.client_auth.verify(1, *A)).status, VerifyStatus.OK)

    async def test_lockout_per_order_blocks_account_rotation(self):
        for tg in range(10, 20):                       # 10 разных аккаунтов перебирают ИНН одного заказа
            await self.s.client_auth.verify(tg, "WWK-0001", f"{tg:04d}")
        r = await self.s.client_auth.verify(500, *A)   # новый аккаунт, верные данные
        self.assertEqual(r.status, VerifyStatus.LOCKED)

    async def test_order_number_case_insensitive_lockout_key(self):
        for tg in range(10, 20):
            await self.s.client_auth.verify(tg, "wwk-0001", "9999")
        self.assertEqual((await self.s.client_auth.verify(500, "WWK-0001", "0001")).status, VerifyStatus.LOCKED)

    async def test_success_resets_counters(self):
        for _ in range(4):
            await self.s.client_auth.verify(1, "WWK-0001", "9999")
        await self.s.client_auth.verify(1, *A)
        for _ in range(4):
            await self.s.client_auth.verify(1, "WWK-0001", "9999")
        self.assertEqual((await self.s.client_auth.verify(1, *A)).status, VerifyStatus.OK)

    async def test_crm_down_is_not_an_attempt_and_fails_closed(self):
        class Down(MockFourlogistAdapter):
            async def find_order_by_number(self, n):
                raise CrmUnavailable("x")
        s = make_services(self.policy, crm=Down())
        for _ in range(8):
            self.assertEqual((await s.client_auth.verify(1, *A)).status, VerifyStatus.CRM_UNAVAILABLE)
        self.assertEqual(await s.client_auth.list_valid_bindings(1), [])
        self.assertEqual(await s.attempts.count_since("tg:1", s.clock() - s.client_auth.policy.lockout), 0)

    async def test_missing_inn_in_crm_fails_closed(self):
        data = {"clients": {"1": {"name": "N", "tax_id": None}}, "orders": {"5": {"number": "Z-1", "client_id": "1"}},
                "documents": {}, "users": {}}
        s = make_services(self.policy, crm=MockFourlogistAdapter(data))
        for guess in ("0000", "None", "1234"):
            self.assertEqual((await s.client_auth.verify(1, "Z-1", guess)).status,
                             VerifyStatus.INVALID if guess.isdigit() else VerifyStatus.BAD_INPUT)
        self.assertEqual(await s.client_auth.list_valid_bindings(1), [])


class BindingTests(Base):
    async def test_multiple_organizations_for_one_telegram_id(self):
        await self.s.client_auth.verify(1, *A)
        await self.s.client_auth.verify(1, *B)
        orgs = {b.org_key for b in await self.s.client_auth.list_valid_bindings(1)}
        self.assertEqual(orgs, {"1001", "1002"})

    async def test_expired_binding_gives_no_access_and_can_be_renewed(self):
        r = await self.s.client_auth.verify(1, *A)
        self.s.clock.advance(days=181)
        self.assertEqual(await self.s.client_auth.list_valid_bindings(1), [])
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.assert_order_access(1, "1001", "5001")
        again = await self.s.client_auth.verify(1, *A)
        self.assertEqual(again.status, VerifyStatus.OK)
        self.assertEqual(again.binding.id, r.binding.id)
        self.assertEqual(len(await self.s.client_auth.list_valid_bindings(1)), 1)

    async def test_revoked_binding_denied_and_cannot_be_reverified(self):
        await self.s.employees.bootstrap_admins([100])
        admin = await self.s.employees.authenticate(100)
        r = await self.s.client_auth.verify(1, *A)
        await self.s.client_auth.revoke_binding(admin, r.binding.id, self.s.employees)
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.assert_order_access(1, "1001", "5001")
        self.assertEqual((await self.s.client_auth.verify(1, *A)).status, VerifyStatus.REVOKED)
        self.assertEqual(await self.s.client_auth.list_valid_bindings(1), [])

    async def test_manager_approval_flow(self):
        policy = dataclasses.replace(self.policy, require_manager_approval=True)
        s = make_services(policy)
        await s.employees.bootstrap_admins([100])
        admin = await s.employees.authenticate(100)
        r = await s.client_auth.verify(1, *A)
        self.assertEqual(r.status, VerifyStatus.PENDING_APPROVAL)
        with self.assertRaises(AccessDenied):                       # до подтверждения доступа нет
            await s.client_auth.assert_order_access(1, "1001", "5001")
        pending = await s.client_auth.list_pending(admin, s.employees)
        self.assertEqual(len(pending), 1)
        await s.client_auth.approve_binding(admin, pending[0].id, s.employees)
        self.assertEqual((await s.client_auth.assert_order_access(1, "1001", "5001")).number, "WWK-0001")

    async def test_logistician_cannot_approve(self):
        await self.s.employees.bootstrap_admins([100])
        admin = await self.s.employees.authenticate(100)
        lg = await self.s.employees.register(admin, 300, "Логист", [Role.LOGISTICIAN])
        r = await self.s.client_auth.verify(1, *A)
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.revoke_binding(lg, r.binding.id, self.s.employees)


class AccessIsolationTests(Base):
    async def asyncSetUp(self):
        await self.s.client_auth.verify(1, *A)   # tg1 -> клиент 1001
        await self.s.client_auth.verify(2, *B)   # tg2 -> клиент 1002

    async def test_cannot_view_foreign_order(self):
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.assert_order_access(1, "1001", "5002")    # заказ клиента 1002
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.assert_order_access(1, "1002", "5002")    # чужая org_key из callback
        self.assertEqual((await self.s.client_auth.assert_order_access(2, "1002", "5002")).number, "WWK-0002")

    async def test_search_by_number_hides_foreign_orders(self):
        self.assertIsNone(await self.s.client_auth.find_order_for_client(1, "1001", "WWK-0002"))
        self.assertIsNone(await self.s.client_auth.find_order_for_client(1, "1001", "NOPE"))
        found = await self.s.client_auth.find_order_for_client(1, "1001", "WWK-0001")
        self.assertEqual(found.order_id, "5001")
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.find_order_for_client(3, "1001", "WWK-0001")   # нет привязки

    async def test_unknown_order_id_denied(self):
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.assert_order_access(1, "1001", "424242")

    async def test_documents_only_own_and_only_visible_groups(self):
        docs = await self.s.client_auth.list_client_documents(1, "1001", "5001")
        self.assertEqual([d.document_id for d in docs], ["9001"])          # внутренний расчёт (internal) скрыт
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.list_client_documents(1, "1001", "5002")
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.get_client_document(1, "1001", "5001", "9002")   # internal
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.get_client_document(1, "1001", "5001", "9003")   # документ чужого заказа
        ok = await self.s.client_auth.get_client_document(1, "1001", "5001", "9001")
        self.assertTrue(ok.content.startswith(b"%PDF"))

    async def test_documents_hidden_by_default_policy(self):
        s = make_services(ClientAuthPolicy())     # CLIENT_VISIBLE_DOC_GROUPS пуст
        await s.client_auth.verify(1, *A)
        self.assertEqual(await s.client_auth.list_client_documents(1, "1001", "5001"), [])
        with self.assertRaises(AccessDenied):
            await s.client_auth.get_client_document(1, "1001", "5001", "9001")

    async def test_oversized_document_rejected(self):
        s = make_services(dataclasses.replace(self.policy, max_document_bytes=5))
        await s.client_auth.verify(1, *A)
        with self.assertRaises(DocumentTooLarge):
            await s.client_auth.get_client_document(1, "1001", "5001", "9001")

    async def test_denials_are_audited(self):
        with self.assertRaises(AccessDenied):
            await self.s.client_auth.assert_order_access(1, "1001", "5002")
        self.assertTrue(any(e.action == "client.access_denied" and e.actor == "client:1" for e in self.s.audit.entries))


class NormalizeTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_order_number("  WWK-0001  "), "WWK-0001")
        self.assertEqual(normalize_order_number("WWK   0001"), "WWK 0001")
        self.assertIsNone(normalize_order_number("x'; DROP"))
        self.assertIsNone(normalize_order_number(""))


if __name__ == "__main__":
    unittest.main()
