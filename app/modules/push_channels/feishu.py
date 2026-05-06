import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

import requests

from app.modules.push_channels.base import BaseChannel
from app.modules.push_channels.registry import ChannelRegistry
from app.utils.logger import get_logger
from config import Config

logger = get_logger("push.feishu")

# 飞书 tenant_access_token 缓存
_feishu_token_cache = None
_feishu_token_expire_at = 0


def get_feishu_tenant_access_token() -> Optional[str]:
    """获取飞书 tenant_access_token（带缓存）"""
    global _feishu_token_cache, _feishu_token_expire_at

    now = datetime.now().timestamp()

    if _feishu_token_cache and now < _feishu_token_expire_at - 300:
        return _feishu_token_cache

    if not Config.FEISHU_APP_ID or not Config.FEISHU_APP_SECRET:
        logger.warning("飞书 APP_ID 或 APP_SECRET 未配置")
        return None

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": Config.FEISHU_APP_ID, "app_secret": Config.FEISHU_APP_SECRET}

    try:
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()

        if data.get("code") == 0:
            _feishu_token_cache = data["tenant_access_token"]
            _feishu_token_expire_at = now + data["expire"]
            return _feishu_token_cache
        else:
            logger.error("飞书 token 获取失败: %s", data.get("msg"))
            return None
    except Exception as e:
        logger.error("飞书 token 请求异常: %s", e)
        return None


def upload_image_to_feishu(image_path: str) -> Optional[str]:
    """上传图片到飞书并返回 image_key"""
    token = get_feishu_tenant_access_token()
    if not token:
        return None

    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        path = Path(image_path)
        if not path.exists():
            logger.warning("图片文件不存在: %s", image_path)
            return None

        files = {"image": (path.name, path.read_bytes(), "image/png")}
        data = {"image_type": "message"}

        resp = requests.post(url, headers=headers, data=data, files=files, timeout=30)
        result = resp.json()

        if result.get("code") == 0:
            return result.get("data", {}).get("image_key")
        else:
            logger.error("图片上传失败: code=%s", result.get("code"))
            return None
    except Exception as e:
        logger.error("图片上传异常: %s", e)
        return None


@ChannelRegistry.register
class FeishuChannel(BaseChannel):
    """飞书推送渠道"""

    channel_name = "feishu"

    def send(self, content_data: Dict[str, Any]) -> bool:
        content_type = content_data.get("type", "unknown")

        if content_type == "video":
            return self._send_video(content_data)
        elif content_type == "dynamic":
            return self._send_dynamic(content_data)
        else:
            logger.warning("未知的推送类型: %s", content_type)
            return False

    def _send_video(self, content_data: Dict[str, Any]) -> bool:
        """推送视频消息"""
        title = content_data.get("title", "无标题")
        uploader_name = content_data.get("uploader_name", "")
        summary = content_data.get("summary", "")
        url = content_data.get("url", "")
        tags = content_data.get("tags", [])
        stocks = content_data.get("stocks", [])
        doc_url = content_data.get("doc_url", "")

        if uploader_name:
            text = f"📺 [{uploader_name}]{title}\n\n"
        else:
            text = f"📺 {title}\n\n"
        if summary:
            text += f"{summary}\n\n"
        if stocks:
            text += f"📈 涉及股票: {'、'.join(stocks)}\n\n"
        if tags:
            text += f"标签: {' '.join([f'#{t}' for t in tags])}\n\n"
        text += f"🔗 原视频: {url}"
        if doc_url:
            text += f"\n📄 详细总结: {doc_url}"

        return self._send_text(text)

    def _send_dynamic(self, content_data: Dict[str, Any]) -> bool:
        """推送动态消息（使用卡片）"""
        title = content_data.get("title", "")
        uploader_name = content_data.get("uploader_name", "")
        text = content_data.get("text", "")
        url = content_data.get("url", "")
        pub_time = content_data.get("pub_time", "")
        images = content_data.get("images", []) or []

        # 格式化时间
        if pub_time:
            try:
                dt = datetime.strptime(pub_time, "%Y-%m-%d %H:%M:%S")
                pub_time_str = dt.strftime("%Y年%m月%d日 %H:%M:%S")
            except (ValueError, TypeError):
                pub_time_str = pub_time
        else:
            pub_time_str = ""

        # 截断文本
        display_text = text[:1000]
        if len(text) > 1000:
            display_text += "..."

        # 上传所有图片获取 image_key
        image_keys = []
        for img_path in images[:4]:
            image_key = upload_image_to_feishu(img_path)
            if image_key:
                image_keys.append(image_key)

        # 构建卡片元素

        # 标题（包含UP主名字）
        if uploader_name:
            title_text = f"📝 [{uploader_name}]{title}" if title else f"📝 [{uploader_name}]新动态"
        else:
            title_text = f"📝 {title}" if title else "📝 新动态"

        elements = []

        # 标题（如果有）
        if title:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{title_text}**"}
            })

        # 文本内容
        if display_text:
            elements.append({
                "tag": "div",
                "text": {"tag": "plain_text", "content": display_text}
            })

        # 添加图片
        for key in image_keys:
            elements.append({"tag": "img", "img_key": key})

        # 时间
        if pub_time_str:
            elements.append({
                "tag": "div",
                "text": {"tag": "plain_text", "content": f"⏰ {pub_time_str}"},
                "text_align": "left"
            })

        # 链接 - 使用 lark_md 格式
        if url:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"[🔗 查看原动态]({url})"}
            })

        # 构建卡片
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "text": title_text},
                "template": "blue"
            },
            "elements": elements
        }

        return self._send_card(card)

    def _send_text(self, text: str) -> bool:
        """发送纯文本消息"""
        token = get_feishu_tenant_access_token()
        if not token:
            return False

        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={Config.FEISHU_RECEIVE_ID_TYPE}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        content_json = json.dumps({"text": text}, ensure_ascii=False)
        payload = {
            "receive_id": Config.FEISHU_RECEIVE_ID,
            "msg_type": "text",
            "content": content_json
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            data = resp.json()
            return data.get("code") == 0
        except Exception as e:
            logger.error("飞书文本推送异常: %s", e)
            return False

    def _send_card(self, card: Dict[str, Any]) -> bool:
        """发送卡片消息"""
        token = get_feishu_tenant_access_token()
        if not token:
            return False

        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={Config.FEISHU_RECEIVE_ID_TYPE}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        payload = {
            "receive_id": Config.FEISHU_RECEIVE_ID,
            "msg_type": "interactive",
            "content": json.dumps(card, ensure_ascii=False)
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            data = resp.json()

            if data.get("code") == 0:
                logger.info("飞书卡片消息推送成功")
                return True
            else:
                logger.error("飞书卡片推送失败: code=%s, msg=%s", data.get("code"), data.get("msg"))
                return False
        except Exception as e:
            logger.error("飞书卡片推送异常: %s", e)
            return False

    def send_text(self, text: str) -> bool:
        """发送纯文本"""
        return self._send_text(text)
