"""自动重新登录模块测试"""
import pytest
from unittest.mock import patch, Mock, call
from threading import Thread

from app.modules.auto_relogin import (
    start_auto_relogin,
    start_auto_relogin_thread,
    _notify_relogin_needed,
    _notify_relogin_expired,
    _notify_relogin_success,
)


class TestAutoRelogin:
    """测试自动重新登录"""

    @patch("app.modules.auto_relogin.push_content")
    @patch("app.modules.auto_relogin.generate_qrcode")
    @patch("app.modules.auto_relogin.poll_login_status")
    @patch("app.modules.auto_relogin.save_login_to_env")
    def test_relogin_success(
        self, mock_save, mock_poll, mock_generate, mock_push
    ):
        mock_generate.return_value = ("key123", "https://example.com/qr")
        mock_poll.return_value = (True, "refresh_token", "cookie_value")
        mock_save.return_value = True

        result = start_auto_relogin()

        assert result is True
        mock_poll.assert_called_once_with("key123")
        mock_save.assert_called_once_with("refresh_token", "cookie_value")
        # 应该发送成功通知
        assert mock_push.called

    @patch("app.modules.auto_relogin.push_content")
    @patch("app.modules.auto_relogin.generate_qrcode")
    @patch("app.modules.auto_relogin.poll_login_status")
    def test_relogin_expired_then_success(
        self, mock_poll, mock_generate, mock_push
    ):
        mock_generate.return_value = ("key123", "https://example.com/qr")
        # 第一次过期，第二次成功
        mock_poll.side_effect = [
            (False, "", ""),
            (True, "refresh_token", "cookie")
        ]

        result = start_auto_relogin()

        assert result is True
        assert mock_poll.call_count == 2
        assert mock_generate.call_count == 2

    @patch("app.modules.auto_relogin.push_content")
    @patch("app.modules.auto_relogin.generate_qrcode")
    @patch("time.sleep")
    def test_relogin_generate_fails_then_succeeds(self, mock_sleep, mock_generate, mock_push):
        # 第一次失败，第二次成功
        mock_generate.side_effect = [None, ("key123", "https://example.com/qr")]
        mock_push.return_value = None

        # start_auto_relogin 内部有 try/except 处理，测试它不会无限循环
        # 通过 mock generate 在第二次成功后退出


class TestNotifications:
    """测试通知函数"""

    @patch("app.modules.auto_relogin.get_enabled_channels")
    @patch("app.modules.auto_relogin.push_content")
    def test_notify_relogin_needed(self, mock_push, mock_channels):
        mock_channels.return_value = ["feishu"]

        _notify_relogin_needed("https://example.com/login", attempt=1)

        mock_push.assert_called_once()
        call_args = mock_push.call_args
        assert call_args[0][0]["type"] == "cookie_error"
        assert "过期" in call_args[0][0]["title"]

    @patch("app.modules.auto_relogin.get_enabled_channels")
    @patch("app.modules.auto_relogin.push_content")
    def test_notify_relogin_expired(self, mock_push, mock_channels):
        mock_channels.return_value = ["feishu"]

        _notify_relogin_expired(attempt=2)

        mock_push.assert_called_once()
        call_args = mock_push.call_args
        assert "过期" in call_args[0][0]["title"]

    @patch("app.modules.auto_relogin.get_enabled_channels")
    @patch("app.modules.auto_relogin.push_content")
    def test_notify_relogin_success(self, mock_push, mock_channels):
        mock_channels.return_value = ["feishu"]

        _notify_relogin_success()

        mock_push.assert_called_once()
        call_args = mock_push.call_args
        assert "成功" in call_args[0][0]["title"]


class TestStartThread:
    """测试线程启动"""

    @patch("app.modules.auto_relogin.start_auto_relogin")
    def test_start_auto_relogin_thread(self, mock_relogin):
        mock_relogin.return_value = None

        t = start_auto_relogin_thread()

        assert isinstance(t, Thread)
        assert t.daemon is True
        assert t.name == "auto-relogin"