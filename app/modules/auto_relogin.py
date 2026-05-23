"""
自动重新登录模块

当 Cookie 过期时，自动发起登录流程，等待用户授权后获取新 Cookie。
"""
import time
from threading import Thread
from typing import Callable, Optional

from app.utils.logger import get_logger
from app.modules.login import generate_qrcode, poll_login_status, save_login_to_env
from app.modules.push_channels import get_enabled_channels, push_content

logger = get_logger("auto_relogin")


def _notify_relogin_needed(qrcode_url: str, attempt: int = 1):
    """通知用户需要重新登录"""
    channels = get_enabled_channels()
    push_content({
        "type": "cookie_error",
        "title": "B站 Cookie 已过期，请重新登录",
        "text": f"请点击以下链接登录 B站（链接 6 分钟内有效）\n\n{qrcode_url}\n\n第 {attempt} 次登录尝试",
        "url": qrcode_url,
    }, channels)


def _notify_relogin_expired(attempt: int):
    """通知用户链接已过期，正在刷新"""
    channels = get_enabled_channels()
    push_content({
        "type": "cookie_error",
        "title": "登录链接已过期，正在刷新...",
        "text": f"第 {attempt} 次尝试，请查看新链接",
        "url": "",
    }, channels)


def _notify_relogin_success():
    """通知用户登录成功"""
    channels = get_enabled_channels()
    push_content({
        "type": "cookie_error",
        "title": "B站 Cookie 刷新成功",
        "text": "Cookie 已自动刷新并保存，程序将自动使用新 Cookie",
        "url": "",
    }, channels)


def start_auto_relogin(on_success: Optional[Callable] = None):
    """
    开始自动重新登录流程
    会一直轮询直到用户成功授权

    Args:
        on_success: 登录成功后调用的回调函数
    """
    logger.info("[自动登录] 开始自动重新登录流程")
    attempt = 0

    while True:
        attempt += 1
        logger.info(f"[自动登录] 第 {attempt} 次尝试...")

        # 生成二维码
        result = generate_qrcode()
        if not result:
            logger.error("[自动登录] 生成二维码失败，5 秒后重试...")
            time.sleep(5)
            continue

        qrcode_key, qrcode_url = result

        # 通知用户
        _notify_relogin_needed(qrcode_url, attempt)

        # 轮询登录状态
        success, refresh_token, cookie = poll_login_status(qrcode_key)

        if success:
            logger.info("[自动登录] 登录成功！")
            save_login_to_env(refresh_token, cookie)
            _notify_relogin_success()

            # 调用成功回调
            if on_success:
                try:
                    on_success()
                except Exception as e:
                    logger.error(f"[自动登录] 成功回调执行失败: {e}")

            return True

        else:
            logger.info(f"[自动登录] 第 {attempt} 次尝试失败（链接过期），继续...")
            _notify_relogin_expired(attempt)
            time.sleep(2)  # 稍作休息，避免过快重试


def start_auto_relogin_thread(on_success: Optional[Callable] = None):
    """启动自动重新登录线程（不阻塞主线程）

    Args:
        on_success: 登录成功后调用的回调函数
    """
    def _target():
        start_auto_relogin(on_success=on_success)

    t = Thread(target=_target, daemon=True, name="auto-relogin")
    t.start()
    logger.info("[自动登录] 自动登录线程已启动")
    return t