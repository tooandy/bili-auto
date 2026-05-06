from typing import Dict, Any
import requests

from app.modules.push_channels.base import BaseChannel
from app.modules.push_channels.registry import ChannelRegistry
from app.utils.logger import get_logger
from config import Config

logger = get_logger("push.wechat")


@ChannelRegistry.register
class WechatChannel(BaseChannel):
    """微信企业号推送渠道（直接调用微信 API）"""

    channel_name = "wechat"

    def send(self, content_data: Dict[str, Any]) -> bool:
        """推送消息到微信企业号"""
        webhook_key = Config.WECHAT_WEBHOOK_KEY
        if not webhook_key:
            logger.debug("微信企业号未配置 WECHAT_WEBHOOK_KEY")
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
        description = (summary or content_data.get("text", ""))[:200]
        picurl = (content_data.get("image_urls") or [""])[0]

        if uploader_name:
            title = f"📺 [{uploader_name}]{title}"
        else:
            title = f"📺 {title}"
        return self._send_article(title, description, url, picurl)

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

        description = display_text
        if pub_time:
            description = f"⏰ {pub_time}\n\n{description}"

        if uploader_name:
            title = f"📝 [{uploader_name}]{title}" if title else f"📝 [{uploader_name}]新动态"
        else:
            title = f"📝 {title}" if title else "📝 新动态"
        picurl = (content_data.get("image_urls") or [""])[0]
        return self._send_article(title, description[:200], url, picurl)

    def _send_article(self, title: str, description: str, url: str, picurl: str) -> bool:
        """发送图文消息到企业微信机器人"""
        webhook_key = Config.WECHAT_WEBHOOK_KEY
        api_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key={webhook_key}"

        payload = {
            "msgtype": "news",
            "news": {
                "articles": [
                    {"title": title, "description": description, "url": url, "picurl": picurl}
                ]
            },
        }

        try:
            resp = requests.post(api_url, json=payload, timeout=15)
            result = resp.json()
            if result.get("errcode") == 0:
                logger.debug("微信推送成功")
                return True
            logger.warning("微信推送失败: %s", result.get("errmsg"))
            return False
        except Exception as e:
            logger.error("微信推送异常: %s", e)
            return False