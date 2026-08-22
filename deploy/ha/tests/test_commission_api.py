"""Secret-safe HTTP client contracts for HA commissioning."""

from __future__ import annotations

import io
import unittest
import urllib.error
from unittest import mock

from deploy.ha import commission_api


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _limit: int) -> bytes:
        return self.body


class CommissionApiTests(unittest.TestCase):
    @mock.patch("deploy.ha.commission_api.urllib.request.urlopen")
    def test_request_uses_stable_product_client_identifier(self, urlopen: mock.Mock) -> None:
        urlopen.return_value = _Response(b'{"ok":true}')

        result = commission_api.request_json(
            "https://witness.example.test/v1/test", "x" * 64, body={"test": True}
        )

        self.assertEqual(result, {"ok": True})
        request = urlopen.call_args.args[0]
        headers = dict(request.header_items())
        self.assertEqual(headers["User-agent"], "Masterplan-Optimiser-HA/1")
        self.assertEqual(headers["Authorization"], "Bearer " + "x" * 64)

    @mock.patch("deploy.ha.commission_api.urllib.request.urlopen")
    def test_cloudflare_numeric_error_is_reported_without_response_details(
        self, urlopen: mock.Mock
    ) -> None:
        urlopen.side_effect = urllib.error.HTTPError(
            "https://witness.example.test/v1/test",
            403,
            "Forbidden",
            {},
            io.BytesIO(b"error code: 1010\nprivate-provider-detail"),
        )

        with self.assertRaisesRegex(
            commission_api.RemoteApiError,
            r"remote API returned HTTP 403 \(provider error 1010\)",
        ) as raised:
            commission_api.request_json(
                "https://witness.example.test/v1/test", "secret-token", body={}
            )

        self.assertNotIn("private-provider-detail", str(raised.exception))
        self.assertNotIn("secret-token", str(raised.exception))

    @mock.patch("deploy.ha.commission_api.witness")
    def test_pair_open_conflict_is_a_retryable_witness_wait(
        self, witness: mock.Mock
    ) -> None:
        witness.side_effect = commission_api.RemoteApiError(
            409, "remote API returned HTTP 409"
        )

        with mock.patch(
            "sys.argv",
            [
                "commission_api.py",
                "witness",
                "pair-open",
                "https://witness.example.test",
                "mp-opt-example",
            ],
        ), mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(commission_api.main(), 10)

        self.assertEqual(stderr.getvalue().strip(), "remote API returned HTTP 409")

    @mock.patch("deploy.ha.commission_api.witness")
    def test_other_witness_conflict_remains_a_failure(self, witness: mock.Mock) -> None:
        witness.side_effect = commission_api.RemoteApiError(
            409, "remote API returned HTTP 409"
        )

        with mock.patch(
            "sys.argv",
            [
                "commission_api.py",
                "witness",
                "join",
                "https://witness.example.test",
                "mp-opt-example",
            ],
        ), mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(commission_api.main(), 1)
