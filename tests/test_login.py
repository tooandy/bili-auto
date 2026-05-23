"""登录模块测试"""
import pytest
from unittest.mock import patch, Mock

from app.modules.login import (
    generate_qrcode,
    poll_login_status,
    parse_cookie_to_dict,
    build_cookie_from_dict,
    save_login_to_env,
    CODE_WAIT_SCAN,
    CODE_SCANNED,
    CODE_EXPIRED,
    CODE_SUCCESS,
)


class TestGenerateQrcode:
    """测试 generate_qrcode"""

    @patch("requests.get")
    def test_generate_success(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": 0,
            "data": {"qrcode_key": "test_key_123"}
        }
        mock_get.return_value = mock_resp

        result = generate_qrcode()

        assert result is not None
        qrcode_key, url = result
        assert qrcode_key == "test_key_123"
        assert "test_key_123" in url

    @patch("requests.get")
    def test_generate_http_error(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        result = generate_qrcode()
        assert result is None

    @patch("requests.get")
    def test_generate_api_error(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": -1, "message": "error"}
        mock_get.return_value = mock_resp

        result = generate_qrcode()
        assert result is None


class TestPollLoginStatus:
    """测试 poll_login_status"""

    @patch("requests.get")
    def test_poll_success(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "code": 0,
            "data": {
                "refresh_token": "test_refresh_token",
                "cookie": "SESSDATA=test; bili_jct=test"
            }
        }
        mock_resp.cookies = {"SESSDATA": "test"}
        mock_get.return_value = mock_resp

        success, refresh_token, cookie = poll_login_status("test_key", poll_interval=0.1)

        assert success is True
        assert refresh_token == "test_refresh_token"
        assert "SESSDATA" in cookie

    @patch("requests.get")
    def test_poll_expired(self, mock_get):
        mock_resp = Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": CODE_EXPIRED, "data": {}}
        mock_get.return_value = mock_resp

        success, refresh_token, cookie = poll_login_status("test_key", poll_interval=0.1)

        assert success is False
        assert refresh_token == ""
        assert cookie == ""


class TestCookieUtils:
    """测试 Cookie 工具函数"""

    def test_parse_cookie_to_dict(self):
        cookie_str = "SESSDATA=abc123; bili_jct=def456; DedeUserID=123"
        result = parse_cookie_to_dict(cookie_str)

        assert result["SESSDATA"] == "abc123"
        assert result["bili_jct"] == "def456"
        assert result["DedeUserID"] == "123"

    def test_parse_cookie_to_dict_empty(self):
        result = parse_cookie_to_dict("")
        assert result == {}

    def test_build_cookie_from_dict(self):
        cookie_dict = {"SESSDATA": "abc123", "bili_jct": "def456"}
        result = build_cookie_from_dict(cookie_dict)

        assert "SESSDATA=abc123" in result
        assert "bili_jct=def456" in result

    def test_build_cookie_from_dict_empty(self):
        result = build_cookie_from_dict({})
        assert result == ""


class TestSaveLoginToEnv:
    """测试 save_login_to_env"""

    def test_save_basic(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("OLD_VALUE=test\n")

        result = save_login_to_env("new_refresh", "cookie_value", str(env_file))

        assert result is True
        content = env_file.read_text()
        assert 'refresh_token="new_refresh"' in content
        assert 'BILIBILI_COOKIE="cookie_value"' in content

    def test_save_without_cookie(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("")

        result = save_login_to_env("refresh_token_only", None, str(env_file))

        assert result is True
        content = env_file.read_text()
        assert 'refresh_token="refresh_token_only"' in content
        assert "BILIBILI_COOKIE" not in content

    def test_save_creates_file(self, tmp_path):
        # save_login_to_env 使用 Path.cwd() / ".env"，所以需要在正确的目录
        import os
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            result = save_login_to_env("token", "cookie", ".env")
            assert result is True
            assert (tmp_path / ".env").exists()
        finally:
            os.chdir(old_cwd)


class TestStatusCodes:
    """测试状态码常量"""

    def test_status_codes_defined(self):
        assert CODE_WAIT_SCAN == 86101
        assert CODE_SCANNED == 86090
        assert CODE_EXPIRED == 86038
        assert CODE_SUCCESS == 0