import logging
import unittest

from wwk.config import Settings, normalize_database_url
from wwk.logging_setup import RedactingFilter


class ConfigTests(unittest.TestCase):
    def test_db_url_normalization(self):
        self.assertEqual(normalize_database_url("postgres://u:p@h:5432/d"), "postgresql+asyncpg://u:p@h:5432/d")
        self.assertEqual(normalize_database_url("postgresql://u:p@h/d"), "postgresql+asyncpg://u:p@h/d")
        self.assertEqual(normalize_database_url("postgresql://u:p@h/d?sslmode=require"),
                         "postgresql+asyncpg://u:p@h/d?ssl=require")
        self.assertEqual(normalize_database_url("postgresql+asyncpg://u:p@h/d"), "postgresql+asyncpg://u:p@h/d")

    def test_defaults_are_safe(self):
        s = Settings.from_env({})
        self.assertEqual(s.crm_mode, "mock")
        self.assertFalse(s.crm_allow_writes)
        self.assertFalse(s.client_require_manager_approval)
        self.assertEqual(s.client_visible_doc_groups, frozenset())

    def test_parsing(self):
        s = Settings.from_env({"ADMIN_TELEGRAM_IDS": "1, 2;3", "CRM_ALLOW_WRITES": "1",
                               "CLIENT_VISIBLE_DOC_GROUPS": "cmr, act", "CRM_AUTH_SCHEME": ""})
        self.assertEqual(s.admin_telegram_ids, frozenset({1, 2, 3}))
        self.assertTrue(s.crm_allow_writes)
        self.assertEqual(s.client_visible_doc_groups, frozenset({"cmr", "act"}))
        self.assertEqual(s.crm_auth_scheme, "")

    def test_validate(self):
        errs = Settings.from_env({}).validate()
        self.assertTrue(any("BOT_TOKEN" in e for e in errs))
        self.assertTrue(any("DATABASE_URL" in e for e in errs))
        ok = Settings.from_env({"TEAM_BOT_TOKEN": "x", "DATABASE_URL": "postgres://a/b", "ADMIN_TELEGRAM_IDS": "1"})
        self.assertEqual(ok.validate(), [])
        real = Settings.from_env({"TEAM_BOT_TOKEN": "x", "DATABASE_URL": "postgres://a/b",
                                  "ADMIN_TELEGRAM_IDS": "1", "CRM_MODE": "real"})
        self.assertTrue(any("CRM_CLIENT_ID" in e for e in real.validate()))
        oauth = Settings.from_env({"TEAM_BOT_TOKEN": "x", "DATABASE_URL": "postgres://a/b",
                                   "ADMIN_TELEGRAM_IDS": "1", "CRM_MODE": "real",
                                   "CRM_CLIENT_ID": "id", "CRM_CLIENT_SECRET": "secret",
                                   "CRM_USERNAME": "user", "CRM_PASSWORD": "password"})
        self.assertEqual(oauth.validate(), [])

    def test_database_password_is_a_redaction_secret(self):
        s = Settings.from_env({"DATABASE_URL": "postgresql://user:db-secret%2Bvalue@host/db"})
        self.assertIn("db-secret%2Bvalue", s.secrets())
        self.assertIn("db-secret+value", s.secrets())

    def test_secrets_not_in_repr(self):
        s = Settings.from_env({"CRM_TOKEN": "supersecrettoken123", "CRM_CLIENT_SECRET": "client-secret-123",
                               "CRM_PASSWORD": "crm-password-123",
                               "TEAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef",
                               "DATABASE_URL": "postgres://u:dbpass@h/d"})
        r = repr(s)
        for secret in ("supersecrettoken123", "client-secret-123", "crm-password-123", "ABCDEFGHIJKL", "dbpass"):
            self.assertNotIn(secret, r)


class RedactionTests(unittest.TestCase):
    def test_redacts_known_secrets_and_patterns(self):
        f = RedactingFilter(["supersecrettoken123"])
        rec = logging.LogRecord("t", logging.INFO, "", 0, "tok=%s hdr=Bearer abcdefgh12345 bot=123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                                ("supersecrettoken123",), None)
        f.filter(rec)
        out = rec.getMessage()
        self.assertNotIn("supersecrettoken123", out)
        self.assertNotIn("abcdefgh12345", out)
        self.assertNotIn("AAAAAAAAAAAA", out)


if __name__ == "__main__":
    unittest.main()
