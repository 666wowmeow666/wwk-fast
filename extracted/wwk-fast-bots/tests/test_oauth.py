import json
import unittest

from wwk.crm.oauth import OAuthHttpResponse, OAuthTokenProvider
from wwk.crm.client import FourlogistClient, HttpResponse


class FakeOAuthTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def post_json(self, url, payload, timeout):
        self.calls.append((url, dict(payload), timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self):
        pass


def token_response(access, refresh, expires=3600):
    return OAuthHttpResponse(200, json.dumps({
        "token_type": "Bearer", "expires_in": expires,
        "access_token": access, "refresh_token": refresh,
    }).encode())


class OAuthProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_grant_posts_documented_json(self):
        t = FakeOAuthTransport(token_response("access-1", "refresh-1"))
        p = OAuthTokenProvider("https://wwkfast.4logist.com", "cid", "csecret", "login", "password",
                               transport=t)
        self.assertEqual(await p.access_token(), "access-1")
        url, payload, timeout = t.calls[0]
        self.assertEqual(url, "https://wwkfast.4logist.com/oauth/v2/token")
        self.assertEqual(payload, {"grant_type": "password", "client_id": "cid",
                                   "client_secret": "csecret", "username": "login", "password": "password"})
        self.assertEqual(p.authorization_header(), "Bearer access-1")
        self.assertNotIn("password", repr(p))

    async def test_refresh_grant_updates_rotated_tokens(self):
        t = FakeOAuthTransport(token_response("access-1", "refresh-1"),
                               token_response("access-2", "refresh-2"))
        p = OAuthTokenProvider("https://crm.example", "cid", "secret", "user", "pw",
                               transport=t, safety_margin_seconds=0)
        await p.access_token()
        p._expires_at = 0
        self.assertEqual(await p.access_token(), "access-2")
        self.assertEqual(t.calls[1][1], {"grant_type": "refresh_token", "client_id": "cid",
                                         "client_secret": "secret", "refresh_token": "refresh-1"})
        self.assertEqual(p._refresh_token, "refresh-2")

    async def test_invalid_refresh_falls_back_to_password_grant(self):
        t = FakeOAuthTransport(token_response("access-1", "refresh-1"),
                               OAuthHttpResponse(401, b'{"error":"invalid_grant"}'),
                               token_response("access-2", "refresh-2"))
        p = OAuthTokenProvider("https://crm.example", "cid", "secret", "user", "pw",
                               transport=t, safety_margin_seconds=0)
        await p.access_token()
        p._expires_at = 0
        self.assertEqual(await p.access_token(), "access-2")
        self.assertEqual(t.calls[2][1]["grant_type"], "password")

    async def test_read_retries_once_after_401_with_refreshed_access_token(self):
        oauth = FakeOAuthTransport(token_response("old-token", "refresh-1"),
                                   token_response("new-token", "refresh-2"))

        class ApiTransport:
            def __init__(self):
                self.calls = []
            async def request(self, method, url, *, params, form, headers, timeout):
                self.calls.append(headers["Authorization"])
                if len(self.calls) == 1:
                    return HttpResponse(401)
                return HttpResponse(200, b'{"items": []}', {"content-type": "application/json"})

        api = ApiTransport()
        provider = OAuthTokenProvider("https://crm.example", "cid", "secret", "user", "pw",
                                      transport=oauth, safety_margin_seconds=0)
        crm = FourlogistClient("https://crm.example", token_provider=provider, transport=api, max_read_retries=0)
        await crm.list_users()
        self.assertEqual(api.calls, ["Bearer old-token", "Bearer new-token"])
        self.assertEqual(oauth.calls[1][1]["grant_type"], "refresh_token")


if __name__ == "__main__":
    unittest.main()
