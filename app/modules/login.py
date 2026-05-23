"""
B站扫码登录核心模块

提供独立的扫码登录功能，被 cli.py 和 auto_relogin.py 共用。
"""
import time
import requests
from typing import Optional, Tuple

# 登录 API
QRCODE_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QRCODE_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
QRCODE_URL_TEMPLATE = "https://account.bilibili.com/h5/account-h5/auth/scan-web?qrcode_key={qrcode_key}"

# 状态码
CODE_WAIT_SCAN = 86101      # 等待扫码
CODE_SCANNED = 86090        # 已扫码，待确认
CODE_EXPIRED = 86038         # 二维码过期
CODE_SUCCESS = 0             # 登录成功


def generate_qrcode() -> Optional[Tuple[str, str]]:
    """
    生成二维码

    Returns:
        (qrcode_key, url) 或 None
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
    }

    try:
        resp = requests.get(QRCODE_GENERATE_URL, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None

        data = resp.json()
        if data["code"] != 0:
            return None

        qrcode_key = data["data"]["qrcode_key"]
        url = QRCODE_URL_TEMPLATE.format(qrcode_key=qrcode_key)
        return qrcode_key, url

    except Exception:
        return None


def poll_login_status(qrcode_key: str, poll_interval: int = 2) -> Tuple[bool, str, str]:
    """
    轮询登录状态，直到用户确认或二维码过期

    Args:
        qrcode_key: 二维码 key
        poll_interval: 轮询间隔（秒）

    Returns:
        (success, refresh_token, cookie)
        success: 是否登录成功
        refresh_token: refresh_token 字符串
        cookie: 完整的 cookie 字符串
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "Referer": "https://www.bilibili.com/",
    }

    poll_params = {"qrcode_key": qrcode_key}

    while True:
        try:
            resp = requests.get(QRCODE_POLL_URL, params=poll_params, headers=headers, timeout=10)

            if resp.status_code != 200:
                time.sleep(poll_interval)
                continue

            result = resp.json()
            top_code = result.get("code")
            data = result.get("data", {})
            refresh_token = data.get("refresh_token", "") if isinstance(data, dict) else ""

            if top_code == CODE_SUCCESS and refresh_token:
                # 登录成功，提取 cookie
                cookies = resp.cookies
                cookie_dict = {}
                for key in cookies.keys():
                    cookie_dict[key] = cookies.get(key)

                if isinstance(data, dict) and data.get("cookie"):
                    resp_cookie = data.get("cookie")
                    for item in resp_cookie.split(";"):
                        item = item.strip()
                        if "=" in item:
                            k, v = item.split("=", 1)
                            cookie_dict[k.strip()] = v.strip()

                full_cookie = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()]) if cookie_dict else ""
                return True, refresh_token, full_cookie

            elif top_code == CODE_EXPIRED:
                return False, "", ""

            elif top_code == CODE_WAIT_SCAN:
                pass  # 等待中，继续轮询

            elif top_code == CODE_SCANNED:
                pass  # 已扫码，继续轮询

            time.sleep(poll_interval)

        except Exception:
            time.sleep(poll_interval)


def parse_cookie_to_dict(cookie_str: str) -> dict:
    """将 Cookie 字符串解析为字典"""
    cookie_dict = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key] = value
    return cookie_dict


def build_cookie_from_dict(cookie_dict: dict) -> str:
    """从字典构建 Cookie 字符串"""
    return "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])


def save_login_to_env(refresh_token: str, cookie: str = None, env_path: str = ".env") -> bool:
    """
    保存 refresh_token 和 cookie 到 .env 文件

    Args:
        refresh_token: refresh_token
        cookie: cookie 字符串（可选）
        env_path: .env 文件路径

    Returns:
        是否成功
    """
    from pathlib import Path

    try:
        env_file = Path(env_path)
        if not env_file.exists():
            env_file = Path.cwd() / ".env"

        env_lines = []
        if env_file.exists():
            with open(env_file, "r", encoding="utf-8") as f:
                env_lines = f.readlines()

        found_refresh = False
        found_cookie = False
        for i, line in enumerate(env_lines):
            if line.startswith("refresh_token="):
                env_lines[i] = f'refresh_token="{refresh_token}"\n'
                found_refresh = True
            elif line.startswith("BILIBILI_COOKIE=") and cookie:
                env_lines[i] = f'BILIBILI_COOKIE="{cookie}"\n'
                found_cookie = True

        if not found_refresh:
            env_lines.append(f'refresh_token="{refresh_token}"\n')
        if not found_cookie and cookie:
            env_lines.append(f'BILIBILI_COOKIE="{cookie}"\n')

        with open(env_file, "w", encoding="utf-8") as f:
            f.writelines(env_lines)

        return True

    except Exception:
        return False