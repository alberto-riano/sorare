from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from web_services import token_service


class TokenServiceTests(TestCase):
    def test_refresh_with_otp_rejects_invalid_code_without_contacting_sorare(self):
        with patch.object(token_service, "begin_refresh") as begin:
            result = token_service.refresh_with_otp("12ab")

        self.assertEqual(result["status"], "error")
        begin.assert_not_called()

    @patch.object(token_service, "finish_refresh", return_value={"status": "success"})
    @patch.object(
        token_service,
        "begin_refresh",
        return_value={"status": "mfa_required", "otp_session_challenge": "challenge"},
    )
    def test_refresh_with_otp_completes_challenge_in_one_action(self, begin, finish):
        result = token_service.refresh_with_otp("123456")

        self.assertEqual(result, {"status": "success"})
        begin.assert_called_once_with()
        finish.assert_called_once_with("challenge", "123456")


class TokenRefreshViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="burguis", password="test-password")
        self.client.force_login(self.user)

    @patch("dashboard.views.token_service.token_status", return_value={"valid": False})
    @patch("dashboard.views.token_service.refresh_with_otp", return_value={"status": "success"})
    def test_single_form_renews_and_redirects(self, refresh, _status):
        response = self.client.post(reverse("refresh_token"), {"otp_code": "123456"})

        self.assertRedirects(response, reverse("refresh_token"), fetch_redirect_response=False)
        refresh.assert_called_once_with("123456")
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn("Token de Sorare renovado y guardado correctamente.", messages)
