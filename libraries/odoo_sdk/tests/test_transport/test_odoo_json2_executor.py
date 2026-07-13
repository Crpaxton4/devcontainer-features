import json
import unittest
import urllib.error
import urllib.request
from http.client import HTTPMessage
from io import BytesIO
from unittest.mock import MagicMock, Mock, patch

from odoo_sdk.transport.errors import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooMissingRecordError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)
from odoo_sdk.transport.json2 import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    OdooJson2Executor,
)


def _make_response(body: dict | list | str, status: int = 200):
    """Return a mock response context manager for urlopen."""
    raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = Mock(return_value=False)
    return mock_resp


def _make_http_error(body: dict | str, code: int):
    """Return a urllib.error.HTTPError with the given body and status code."""
    raw = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
    fp = BytesIO(raw)
    return urllib.error.HTTPError(
        url="http://example.com",
        code=code,
        msg=f"HTTP {code}",
        hdrs=HTTPMessage(),
        fp=fp,
    )


class TestOdooJson2ExecutorConstructor(unittest.TestCase):
    def test_stores_url_without_trailing_slash(self) -> None:
        ex = OdooJson2Executor("https://example.com/", "mydb", "key123")
        self.assertEqual(ex._url, "https://example.com")

    def test_stores_db(self) -> None:
        ex = OdooJson2Executor("https://example.com", "mydb", "key123")
        self.assertEqual(ex._db, "mydb")

    def test_stores_api_key_privately(self) -> None:
        ex = OdooJson2Executor("https://example.com", "mydb", "key123")
        self.assertEqual(ex._api_key, "key123")

    def test_db_can_be_none(self) -> None:
        ex = OdooJson2Executor("https://example.com", None, "key123")
        self.assertIsNone(ex._db)

    def test_timeout_defaults_to_module_constant(self) -> None:
        ex = OdooJson2Executor("https://example.com", "mydb", "key123")
        self.assertEqual(ex._timeout, DEFAULT_REQUEST_TIMEOUT_SECONDS)

    def test_timeout_can_be_overridden(self) -> None:
        ex = OdooJson2Executor("https://example.com", "mydb", "key123", timeout=5.0)
        self.assertEqual(ex._timeout, 5.0)


class TestOdooJson2ExecutorTimeout(unittest.TestCase):
    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_urlopen_receives_default_timeout(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        ex.execute("res.partner", "search", [])
        self.assertEqual(
            mock_urlopen.call_args.kwargs["timeout"], DEFAULT_REQUEST_TIMEOUT_SECONDS
        )

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_urlopen_receives_configured_timeout(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key", timeout=2.5)
        ex.execute("res.partner", "search", [])
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 2.5)


class TestOdooJson2ExecutorRequest(unittest.TestCase):
    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_search_sends_correct_post_url(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([1, 2, 3])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        ex.execute("res.partner", "search", [("active", "=", True)])
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(
            request.full_url, "https://example.com/json/2/res.partner/search"
        )
        self.assertEqual(request.method, "POST")

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_search_sends_authorization_header(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "mykey")
        ex.execute("res.partner", "search", [])
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.get_header("Authorization"), "Bearer mykey")

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_search_sends_content_type_header(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        ex.execute("res.partner", "search", [])
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(
            request.get_header("Content-type"), "application/json; charset=utf-8"
        )

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_search_sends_database_header_when_db_given(
        self, mock_urlopen: Mock
    ) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        ex.execute("res.partner", "search", [])
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.get_header("X-odoo-database"), "mydb")

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_db_none_omits_database_header(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", None, "key")
        ex.execute("res.partner", "search", [])
        request = mock_urlopen.call_args[0][0]
        self.assertIsNone(request.get_header("X-odoo-database"))

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_search_body_has_no_ids_field(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        ex.execute("res.partner", "search", domain=[("name", "=", "Bob")])
        request = mock_urlopen.call_args[0][0]
        body = json.loads(request.data)
        self.assertNotIn("ids", body)
        self.assertEqual(body["domain"], [["name", "=", "Bob"]])

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_read_places_ids_from_args(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([{"id": 1}])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        ex.execute("res.partner", "read", [1, 2, 3], fields=["name"])
        request = mock_urlopen.call_args[0][0]
        body = json.loads(request.data)
        self.assertEqual(body["ids"], [1, 2, 3])
        self.assertEqual(body["fields"], ["name"])

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_context_extracted_from_kwargs(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        ctx = {"lang": "en_US"}
        ex.execute("res.partner", "search", domain=[], context=ctx)
        request = mock_urlopen.call_args[0][0]
        body = json.loads(request.data)
        self.assertEqual(body["context"], ctx)
        self.assertNotIn("context", body.get("domain", []))

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_missing_context_defaults_to_empty_dict(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        ex.execute("res.partner", "search", domain=[])
        request = mock_urlopen.call_args[0][0]
        body = json.loads(request.data)
        self.assertEqual(body["context"], {})


class TestOdooJson2ExecutorSuccess(unittest.TestCase):
    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_200_returns_parsed_json_value(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([1, 2, 3])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        result = ex.execute("res.partner", "search", domain=[])
        self.assertEqual(result, [1, 2, 3])

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_200_with_object_response(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response({"id": 5, "name": "Acme"})
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        result = ex.execute("res.partner", "read", [5], fields=["name"])
        self.assertEqual(result, {"id": 5, "name": "Acme"})


class TestOdooJson2ExecutorErrors(unittest.TestCase):
    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_401_raises_authentication_error(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = _make_http_error(
            {"name": "odoo.exceptions.AccessDenied", "message": "bad credentials"}, 401
        )
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooAuthenticationError):
            ex.execute("res.partner", "search", domain=[])

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_403_raises_access_error(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = _make_http_error(
            {"name": "odoo.exceptions.AccessError", "message": "forbidden"}, 403
        )
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooAccessError):
            ex.execute("res.partner", "search", domain=[])

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_404_raises_missing_record_error(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = _make_http_error(
            {"name": "odoo.exceptions.MissingError", "message": "gone"}, 404
        )
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooMissingRecordError):
            ex.execute("res.partner", "read", [99], fields=[])

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_422_raises_validation_error(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = _make_http_error(
            {"name": "odoo.exceptions.ValidationError", "message": "invalid"}, 422
        )
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooValidationError):
            ex.execute("res.partner", "write", [1], vals={})

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_500_raises_server_error_with_message(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = _make_http_error(
            {"name": "odoo.exceptions.UserError", "message": "boom on server"}, 500
        )
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooServerError) as caught:
            ex.execute("res.partner", "search", domain=[])
        self.assertEqual(str(caught.exception), "boom on server")

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_non_json_response_raises_transport_error(self, mock_urlopen: Mock) -> None:
        err = urllib.error.HTTPError(
            url="http://example.com",
            code=500,
            msg="Internal Server Error",
            hdrs=HTTPMessage(),
            fp=BytesIO(b"<html>Bad Gateway</html>"),
        )
        mock_urlopen.side_effect = err
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooTransportError):
            ex.execute("res.partner", "search", domain=[])

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_url_error_raises_transport_error(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError(reason="connection refused")
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooTransportError):
            ex.execute("res.partner", "search", domain=[])

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_api_key_not_in_authentication_error_message(
        self, mock_urlopen: Mock
    ) -> None:
        mock_urlopen.side_effect = _make_http_error(
            {"name": "odoo.exceptions.AccessDenied", "message": "unauthorized"}, 401
        )
        secret = "super-secret-api-key"
        ex = OdooJson2Executor("https://example.com", "mydb", secret)
        with self.assertRaises(OdooAuthenticationError) as caught:
            ex.execute("res.partner", "search", domain=[])
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_api_key_not_in_transport_error_message(self, mock_urlopen: Mock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError(reason="connection refused")
        secret = "super-secret-api-key"
        ex = OdooJson2Executor("https://example.com", "mydb", secret)
        with self.assertRaises(OdooTransportError) as caught:
            ex.execute("res.partner", "search", domain=[])
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_non_json_success_response_raises_transport_error(
        self, mock_urlopen: Mock
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json at all"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = Mock(return_value=False)
        mock_urlopen.return_value = mock_resp
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooTransportError):
            ex.execute("res.partner", "search", domain=[])
