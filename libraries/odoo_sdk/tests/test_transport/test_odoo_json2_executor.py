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


class TestOdooJson2ExecutorPositionalArguments(unittest.TestCase):
    """Cover the positional-to-named body conversion JSON-2 requires.

    Every recordset op calls the executor with the XML-RPC positional convention,
    so each case asserts the emitted body shape for one such call.
    """

    def _body_for(self, model: str, method: str, *args, **kwargs) -> dict:
        """Return the JSON body the executor emits for one positional call."""
        with patch("odoo_sdk.transport.json2.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _make_response([])
            ex = OdooJson2Executor("https://example.com", "mydb", "key")
            ex.execute(model, method, *args, **kwargs)
            return json.loads(mock_urlopen.call_args[0][0].data)

    def test_write_keeps_ids_and_names_vals(self) -> None:
        body = self._body_for("res.partner", "write", [1, 2], {"name": "Bob"})
        self.assertEqual(body["ids"], [1, 2])
        self.assertEqual(body["vals"], {"name": "Bob"})

    def test_create_names_vals_list_not_ids(self) -> None:
        body = self._body_for("res.partner", "create", {"name": "Acme"})
        self.assertEqual(body["vals_list"], {"name": "Acme"})
        self.assertNotIn("ids", body)

    def test_search_names_domain_not_ids(self) -> None:
        body = self._body_for("res.partner", "search", [["name", "=", "Bob"]])
        self.assertEqual(body["domain"], [["name", "=", "Bob"]])
        self.assertNotIn("ids", body)

    def test_search_read_names_domain_and_fields(self) -> None:
        body = self._body_for(
            "res.partner", "search_read", [["active", "=", True]], ["name"]
        )
        self.assertEqual(body["domain"], [["active", "=", True]])
        self.assertEqual(body["fields"], ["name"])
        self.assertNotIn("ids", body)

    def test_search_count_names_domain(self) -> None:
        body = self._body_for("res.partner", "search_count", [["active", "=", True]])
        self.assertEqual(body["domain"], [["active", "=", True]])
        self.assertNotIn("ids", body)

    def test_read_group_names_domain_fields_and_groupby(self) -> None:
        body = self._body_for(
            "account.analytic.line",
            "read_group",
            [["task_id", "=", 7]],
            ["unit_amount:sum"],
            ["date:day"],
            lazy=False,
        )
        self.assertEqual(body["domain"], [["task_id", "=", 7]])
        self.assertEqual(body["fields"], ["unit_amount:sum"])
        self.assertEqual(body["groupby"], ["date:day"])
        self.assertIs(body["lazy"], False)
        self.assertNotIn("ids", body)

    def test_name_search_names_all_four_positionals(self) -> None:
        body = self._body_for(
            "project.project",
            "name_search",
            "Acme",
            [["active", "=", True]],
            "ilike",
            5,
        )
        self.assertEqual(body["name"], "Acme")
        self.assertEqual(body["domain"], [["active", "=", True]])
        self.assertEqual(body["operator"], "ilike")
        self.assertEqual(body["limit"], 5)
        self.assertNotIn("ids", body)

    def test_name_create_names_name(self) -> None:
        body = self._body_for("res.partner", "name_create", "Acme")
        self.assertEqual(body["name"], "Acme")
        self.assertNotIn("ids", body)

    def test_default_get_names_fields_list(self) -> None:
        body = self._body_for("project.task", "default_get", ["stage_id"])
        self.assertEqual(body["fields_list"], ["stage_id"])
        self.assertNotIn("ids", body)

    def test_fields_get_names_allfields_and_attributes(self) -> None:
        body = self._body_for("mail.mail", "fields_get", ["state"], ["type"])
        self.assertEqual(body["allfields"], ["state"])
        self.assertEqual(body["attributes"], ["type"])
        self.assertNotIn("ids", body)

    def test_copy_keeps_ids_and_names_default(self) -> None:
        body = self._body_for("project.task", "copy", 7, {"name": "Clone"})
        self.assertEqual(body["ids"], 7)
        self.assertEqual(body["default"], {"name": "Clone"})

    def test_read_keeps_ids_and_names_fields(self) -> None:
        body = self._body_for("res.partner", "read", [1, 2], ["name"])
        self.assertEqual(body["ids"], [1, 2])
        self.assertEqual(body["fields"], ["name"])

    def test_get_metadata_keeps_ids(self) -> None:
        body = self._body_for("project.task", "get_metadata", [4])
        self.assertEqual(body["ids"], [4])

    def test_unmapped_method_keeps_leading_ids(self) -> None:
        body = self._body_for("project.task", "message_post", [9], body="hello")
        self.assertEqual(body["ids"], [9])
        self.assertEqual(body["body"], "hello")

    def test_keyword_argument_wins_over_positional_of_same_name(self) -> None:
        body = self._body_for("res.partner", "search", [["a", "=", 1]], domain=[])
        self.assertEqual(body["domain"], [])

    def test_context_is_still_extracted_from_positional_call(self) -> None:
        body = self._body_for(
            "res.partner", "write", [1], {"name": "Bob"}, context={"lang": "en_US"}
        )
        self.assertEqual(body["context"], {"lang": "en_US"})

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_excess_positional_args_raise_transport_error(
        self, mock_urlopen: Mock
    ) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooTransportError) as caught:
            ex.execute("res.partner", "write", [1], {"name": "Bob"}, "extra")
        self.assertIn("positional", str(caught.exception).lower())

    @patch("odoo_sdk.transport.json2.urllib.request.urlopen")
    def test_excess_positional_args_send_no_request(self, mock_urlopen: Mock) -> None:
        mock_urlopen.return_value = _make_response([])
        ex = OdooJson2Executor("https://example.com", "mydb", "key")
        with self.assertRaises(OdooTransportError):
            ex.execute("project.task", "message_post", [9], "body-as-positional")
        mock_urlopen.assert_not_called()


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
