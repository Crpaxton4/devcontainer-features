import unittest

from odoo_sdk.transport._http_error_mapping import map_http_error
from odoo_sdk.transport.errors import (
    OdooAccessError,
    OdooAuthenticationError,
    OdooMissingRecordError,
    OdooServerError,
    OdooTransportError,
    OdooValidationError,
)


class TestMapHttpError(unittest.TestCase):
    def _json_body(self, name: str = "", message: str = "msg", debug: str = "") -> str:
        import json

        return json.dumps({"name": name, "message": message, "debug": debug})

    # --- name-field-driven mapping ---

    def test_access_denied_name_raises_authentication_error(self) -> None:
        body = self._json_body(
            name="odoo.exceptions.AccessDenied", message="bad credentials"
        )
        exc = map_http_error(200, body, model="res.users", method="login")
        self.assertIsInstance(exc, OdooAuthenticationError)
        self.assertEqual(str(exc), "bad credentials")

    def test_access_error_name_raises_access_error(self) -> None:
        body = self._json_body(
            name="odoo.exceptions.AccessError", message="access denied"
        )
        exc = map_http_error(200, body)
        self.assertIsInstance(exc, OdooAccessError)

    def test_missing_error_name_raises_missing_record_error(self) -> None:
        body = self._json_body(
            name="odoo.exceptions.MissingError", message="record gone"
        )
        exc = map_http_error(200, body)
        self.assertIsInstance(exc, OdooMissingRecordError)

    def test_validation_error_name_raises_validation_error(self) -> None:
        body = self._json_body(
            name="odoo.exceptions.ValidationError", message="invalid value"
        )
        exc = map_http_error(200, body)
        self.assertIsInstance(exc, OdooValidationError)

    def test_user_error_name_raises_server_error(self) -> None:
        body = self._json_body(name="odoo.exceptions.UserError", message="user message")
        exc = map_http_error(200, body)
        self.assertIsInstance(exc, OdooServerError)

    def test_unknown_name_raises_server_error(self) -> None:
        body = self._json_body(name="some.internal.Error", message="boom")
        exc = map_http_error(500, body)
        self.assertIsInstance(exc, OdooServerError)

    # --- HTTP-status-code fallback when name is absent ---

    def test_401_status_raises_authentication_error(self) -> None:
        body = self._json_body(name="", message="unauthorized")
        exc = map_http_error(401, body)
        self.assertIsInstance(exc, OdooAuthenticationError)

    def test_403_status_raises_access_error(self) -> None:
        body = self._json_body(name="", message="forbidden")
        exc = map_http_error(403, body)
        self.assertIsInstance(exc, OdooAccessError)

    def test_404_status_raises_missing_record_error(self) -> None:
        body = self._json_body(name="", message="not found")
        exc = map_http_error(404, body)
        self.assertIsInstance(exc, OdooMissingRecordError)

    def test_422_status_raises_validation_error(self) -> None:
        body = self._json_body(name="", message="unprocessable")
        exc = map_http_error(422, body)
        self.assertIsInstance(exc, OdooValidationError)

    def test_500_with_no_name_raises_server_error(self) -> None:
        body = self._json_body(name="", message="internal error")
        exc = map_http_error(500, body)
        self.assertIsInstance(exc, OdooServerError)

    # --- name takes priority over HTTP status ---

    def test_name_takes_priority_over_http_status(self) -> None:
        # AccessDenied name with 500 status → should still be OdooAuthenticationError
        body = self._json_body(name="odoo.exceptions.AccessDenied", message="denied")
        exc = map_http_error(500, body)
        self.assertIsInstance(exc, OdooAuthenticationError)

    # --- non-JSON fallback ---

    def test_non_json_body_raises_transport_error(self) -> None:
        exc = map_http_error(500, "<html>Bad Gateway</html>")
        self.assertIsInstance(exc, OdooTransportError)

    def test_non_json_body_truncates_detail_to_500_chars(self) -> None:
        long_body = "x" * 1000
        exc = map_http_error(502, long_body)
        self.assertIsInstance(exc, OdooTransportError)
        assert exc.detail is not None
        self.assertEqual(len(exc.detail), 500)

    def test_empty_body_raises_transport_error(self) -> None:
        exc = map_http_error(500, "")
        self.assertIsInstance(exc, OdooTransportError)

    # --- metadata propagation ---

    def test_model_and_method_stored_on_exception(self) -> None:
        body = self._json_body(name="", message="err")
        exc = map_http_error(401, body, model="res.partner", method="read")
        self.assertEqual(exc.model, "res.partner")
        self.assertEqual(exc.method, "read")

    def test_debug_field_stored_as_detail(self) -> None:
        import json

        body = json.dumps({"name": "", "message": "err", "debug": "traceback here"})
        exc = map_http_error(500, body)
        self.assertEqual(exc.detail, "traceback here")

    def test_message_preserved_in_exception(self) -> None:
        body = self._json_body(
            name="odoo.exceptions.AccessError", message="You are not allowed"
        )
        exc = map_http_error(403, body)
        self.assertEqual(str(exc), "You are not allowed")
