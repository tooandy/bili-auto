from typing import Dict, Any

import requests

from app.modules.push_channels.base import BaseChannel
from app.modules.push_channels.registry import ChannelRegistry
from app.utils.logger import get_logger
from config import Config

logger = get_logger("push.telegram")


@ChannelRegistry.register
class TelegramChannel(BaseChannel):
    """Telegram 推送渠道"""

    channel_name = "telegram"

    def send(self, content_data: Dict[str, Any]) -> bool:
        """推送消息到 Telegram"""
        if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
            logger.debug("Telegram 未配置")
            return False

        content_type = content_data.get("type", "unknown")

        if content_type == "video":
            return self._send_video(content_data)
        elif content_type == "dynamic":
            return self._send_dynamic(content_data)
        else:
            return self._send_video(content_data)

    def _send_video(self, content_data: Dict[str, Any]) -> bool:
        """推送视频消息"""
        title = content_data.get("title", "无标题")
        uploader_name = content_data.get("uploader_name", "")
        summary = content_data.get("summary", "")
        url = content_data.get("url", "")
        doc_url = content_data.get("doc_url", "")

        if uploader_name:
            text = f"📺 [{uploader_name}]{title}\n\n"
        else:
            text = f"📺 {title}\n\n"
        if summary:
            text += f"{summary[:500]}\n\n"
        text += f"🔗 {url}"
        if doc_url:
            text += f"\n📄 {doc_url}"

        return self._send_text(text)

    def _send_dynamic(self, content_data: Dict[str, Any]) -> bool:
        """推送动态消息"""
        title = content_data.get("title", "")
        uploader_name = content_data.get("uploader_name", "")
        text = content_data.get("text", "")
        url = content_data.get("url", "")
        pub_time = content_data.get("pub_time", "")

        # 截断文本
        display_text = text[:500]
        if len(text) > 500:
            display_text += "..."

        # 构建消息
        title_prefix = f"📝 [{uploader_name}]{title}" if (uploader_name and title) else (f"📝 [{uploader_name}]" if uploader_name else ("📝 " + (title if title else "")))
        msg = f"{title_prefix}\n\n"
        msg += f"{display_text}\n\n"
        if pub_time:
            msg += f"⏰ {pub_time}\n"
        msg += f"🔗 {url}"

        return self._send_text(msg)

    def _send_text(self, text: str) -> bool:
        """发送文本消息"""
        url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": Config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            resp = requests.post(url, json=payload, timeout=15)
            result = resp.json()
            return result.get("ok", False)
        except Exception as e:
            logger.error("Telegram 推送异常: %s", e)
            return False
