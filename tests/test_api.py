from http.server import ThreadingHTTPServer
import json
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from evidence_gate.api import load_principals, make_handler
from evidence_gate.core import EvidenceGate, Principal
from evidence_gate.fixture import build_corpus


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.token = "reader-token-for-tests-only"
        cls.admin_token = "admin-token-for-tests-only"
        gate = EvidenceGate(build_corpus())
        principals = {cls.token: Principal("northstar"), cls.admin_token: Principal("northstar", ("admin",))}
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(gate, principals))
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/v1/query"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, payload, token=None):
        request = Request(self.url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_missing_or_wrong_token_is_unauthorized(self):
        for token in (None, "wrong-token", "caf\u00e9-invalid-token"):
            code, body = self.request({"text": "refund", "region": "us"}, token)
            self.assertEqual((code, body), (401, {"error": "unauthorized"}))

    def test_token_derives_principal_and_body_cannot_escalate(self):
        code, body = self.request({"text": "privileged access review interval", "region": "us"}, self.token)
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "abstain")
        code, _ = self.request({"text": "privileged access review interval", "region": "us", "roles": ["admin"]}, self.token)
        self.assertEqual(code, 400)
        code, body = self.request({"text": "privileged access review interval", "region": "us"}, self.admin_token)
        self.assertEqual((code, body["status"]), (200, "answered"))

    def test_api_cannot_select_relevance_only_ablation(self):
        code, _ = self.request({"text": "refund", "region": "us", "mode": "baseline"}, self.token)
        self.assertEqual(code, 400)

    def test_invalid_date_and_region_fail_as_bad_requests(self):
        for payload in ({"text": "refund", "as_of": "yesterday"}, {"text": "refund", "region": "anywhere"}):
            self.assertEqual(self.request(payload, self.token)[0], 400)

    def test_oversized_body_is_rejected(self):
        code, _ = self.request({"text": "x" * 9000, "region": "us"}, self.token)
        self.assertEqual(code, 413)

    def test_bearer_configuration_requires_explicit_identity_mapping(self):
        for value in ("", "{}", '{"short":{"tenant":"northstar","roles":["reader"]}}', '{"token-long-enough":{"tenant":"northstar","roles":[]}}'):
            with self.assertRaises(ValueError):
                load_principals(value)
        principals = load_principals('{"a-long-local-only-token":{"tenant":"northstar","roles":["reader"]}}')
        self.assertEqual(principals["a-long-local-only-token"].tenant, "northstar")


if __name__ == "__main__":
    unittest.main()
