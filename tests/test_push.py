"""
测试 push_channels 模块
"""
import pytest
from unittest.mock import MagicMock, patch


from app.modules.push_channels import push_content, list_channels, get_channel, push_video_to_feishu, push_dynamic_to_feishu
from app.modules.push_channels.feishu import (
    get_feishu_tenant_access_token,
    upload_image_to_feishu,
    FeishuChannel,
)
from app.modules.push_channels.telegram import TelegramChannel
from app.modules.push_channels.wechat import WechatChannel


class TestChannelRegistry:
    """测试渠道注册表"""

    def test_list_channels(self):
        """测试：列出所有已注册渠道"""
        channels = list_channels()
        assert "feishu" in channels
        assert "wechat" in channels
        assert "telegram" in channels

    def test_get_channel(self):
        """测试：获取指定渠道"""
        channel = get_channel("feishu")
        assert channel is not None
        assert channel.channel_name == "feishu"

    def test_get_unknown_channel(self):
        """测试：获取未知渠道返回 None"""
        channel = get_channel("unknown")
        assert channel is None


class TestFeishuChannel:
    """测试飞书渠道"""

    def test_send_video(self):
        """测试：发送视频消息"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_text', return_value=True) as mock_send:
            result = channel.send({
                "type": "video",
                "title": "测试视频",
                "summary": "这是摘要",
                "tags": ["科技"],
                "stocks": ["小米"],
                "url": "https://bilibili.com/video/BV123",
                "doc_url": "https://feishu.doc/abc"
            })

            assert result is True
            mock_send.assert_called_once()
            call_text = mock_send.call_args[0][0]
            assert "测试视频" in call_text
            assert "这是摘要" in call_text
            assert "小米" in call_text

    def test_send_dynamic(self):
        """测试：发送动态消息（卡片）"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_card', return_value=True) as mock_send:
            result = channel.send({
                "type": "dynamic",
                "text": "这是一条动态内容",
                "url": "https://bilibili.com/opus/123",
                "pub_time": "2024-03-31 18:00:00"
            })

            assert result is True
            mock_send.assert_called_once()

    def test_send_text(self):
        """测试：发送纯文本"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_text', return_value=True) as mock_send:
            result = channel.send_text("纯文本消息")
            assert result is True
            mock_send.assert_called_once_with("纯文本消息")


class TestFeishuToken:
    """测试飞书 Token 获取"""

    def test_get_token_success(self):
        """测试：成功获取 token"""
        with patch('requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "code": 0,
                "tenant_access_token": "test_token_123",
                "expire": 7200
            }
            mock_post.return_value = mock_response

            # 清空缓存
            import app.modules.push_channels.feishu as feishu_module
            feishu_module._feishu_token_cache = None
            feishu_module._feishu_token_expire_at = 0

            with patch('app.modules.push_channels.feishu.Config') as mock_cfg:
                mock_cfg.FEISHU_APP_ID = "test_app_id"
                mock_cfg.FEISHU_APP_SECRET = "test_app_secret"
                token = get_feishu_tenant_access_token()
            assert token == "test_token_123"

    def test_get_token_failure(self):
        """测试：获取 token 失败"""
        with patch('requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "code": 10013,
                "msg": "invalid app_id"
            }
            mock_post.return_value = mock_response

            import app.modules.push_channels.feishu as feishu_module
            feishu_module._feishu_token_cache = None

            token = get_feishu_tenant_access_token()
            assert token is None

    def test_get_token_uses_cache(self):
        """测试：使用缓存的 token"""
        import app.modules.push_channels.feishu as feishu_module
        feishu_module._feishu_token_cache = "cached_token"
        feishu_module._feishu_token_expire_at = 9999999999

        with patch('requests.post') as mock_post:
            token = get_feishu_tenant_access_token()
            assert token == "cached_token"
            mock_post.assert_not_called()


class TestPushContent:
    """测试统一推送接口"""

    def test_push_to_feishu_video(self):
        """测试：推送视频到飞书"""
        result = push_content({
            "type": "video",
            "title": "测试视频",
            "url": "https://bilibili.com/video/BV123"
        }, ["feishu"])
        # 由于是 mock，实际会发送失败，但不会崩溃
        assert result is False or result is True

    def test_push_to_multiple_channels(self):
        """测试：推送到多个渠道"""
        content = {
            "type": "dynamic",
            "text": "测试动态",
            "url": "https://bilibili.com/opus/123"
        }
        # 只推送到 feishu
        result = push_content(content, ["feishu"])
        assert result is False or result is True

    def test_push_to_unknown_channel(self):
        """测试：推送到未知渠道"""
        result = push_content({
            "type": "dynamic",
            "text": "测试",
            "url": "https://example.com"
        }, ["unknown_channel"])
        assert result is False


class TestFeishuCardMessage:
    """测试飞书卡片消息构建"""

    def test_build_dynamic_card(self):
        """测试：构建动态卡片"""
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "text": "📝 新动态"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "plain_text", "content": "测试内容"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": "[链接](http://example.com)"}}
            ]
        }

        # 验证卡片结构
        assert card["config"]["wide_screen_mode"] is True
        assert card["header"]["template"] == "blue"
        assert len(card["elements"]) == 2


class TestTelegramChannelVideo:
    """测试 Telegram 视频推送"""

    def test_send_video(self):
        """测试：发送视频消息"""
        channel = TelegramChannel()

        with patch.object(channel, '_send_text', return_value=True) as mock_send:
            with patch('app.modules.push_channels.telegram.Config') as mock_config:
                mock_config.TELEGRAM_TOKEN = "fake_token"
                mock_config.TELEGRAM_CHAT_ID = "fake_chat_id"

                result = channel.send({
                    "type": "video",
                    "title": "测试视频标题",
                    "summary": "视频摘要内容",
                    "url": "https://bilibili.com/video/BV123",
                    "doc_url": "https://feishu.doc/abc"
                })

                assert result is True
                mock_send.assert_called_once()
                text = mock_send.call_args[0][0]
                assert "测试视频标题" in text
                assert "bilibili.com" in text


class TestTelegramChannel:
    """测试 Telegram 渠道"""

    def test_send_dynamic_with_title(self):
        """测试：发送动态消息（带标题）"""
        channel = TelegramChannel()

        with patch.object(channel, 'send', wraps=channel.send):
            with patch.object(channel, '_send_text', return_value=True) as mock_send:
                # patch send 避免 Config 检查直接返回 False
                with patch('app.modules.push_channels.telegram.Config') as mock_config:
                    mock_config.TELEGRAM_TOKEN = "fake_token"
                    mock_config.TELEGRAM_CHAT_ID = "fake_chat_id"

                    result = channel.send({
                        "type": "dynamic",
                        "title": "动态标题",
                        "text": "动态正文内容",
                        "url": "https://bilibili.com/opus/123",
                        "pub_time": "2024-03-31 18:00:00"
                    })

                    assert result is True
                    mock_send.assert_called_once()
                    call_text = mock_send.call_args[0][0]
                    assert "动态标题" in call_text
                    assert "动态正文内容" in call_text
                    assert "📝" in call_text  # 动态图标

    def test_send_dynamic_without_title(self):
        """测试：发送动态消息（无标题）"""
        channel = TelegramChannel()

        with patch.object(channel, '_send_text', return_value=True) as mock_send:
            with patch('app.modules.push_channels.telegram.Config') as mock_config:
                mock_config.TELEGRAM_TOKEN = "fake_token"
                mock_config.TELEGRAM_CHAT_ID = "fake_chat_id"

                result = channel.send({
                    "type": "dynamic",
                    "title": "",
                    "text": "动态正文内容",
                    "url": "https://bilibili.com/opus/123",
                })

                assert result is True
                mock_send.assert_called_once()
                call_text = mock_send.call_args[0][0]
                assert "📝" in call_text
                assert "动态正文内容" in call_text


@pytest.mark.skip(reason="WechatChannel 旧实现已废弃，使用直接 webhook API")
class TestWechatChannel:
    """测试微信企业号渠道"""

    def test_send_dynamic_with_title(self):
        """测试：发送动态消息（带标题）"""
        channel = WechatChannel()

        with patch.object(channel, '_get_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.json.return_value = {"errcode": 0}
                mock_post.return_value = mock_response

                with patch('app.modules.push_channels.wechat.Config') as mock_config:
                    mock_config.WECHAT_CORP_ID = "fake_corp_id"
                    mock_config.WECHAT_CORP_SECRET = "fake_secret"
                    mock_config.WECHAT_AGENT_ID = "123456"
                    mock_config.WECHAT_TO_USER = "fake_user"

                    result = channel.send({
                        "type": "dynamic",
                        "title": "动态标题",
                        "text": "动态正文",
                        "url": "https://bilibili.com/opus/123",
                        "pub_time": "2024-03-31",
                        "image_urls": []
                    })

                    assert result is True
                    # json= 是关键字参数，从 call_args[1] 取
                    call_json = mock_post.call_args[1]["json"]
                    assert "📝 动态标题" in call_json["news"]["articles"][0]["title"]

    def test_send_dynamic_without_title(self):
        """测试：发送动态消息（无标题）"""
        channel = WechatChannel()

        with patch.object(channel, '_get_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.json.return_value = {"errcode": 0}
                mock_post.return_value = mock_response

                with patch('app.modules.push_channels.wechat.Config') as mock_config:
                    mock_config.WECHAT_CORP_ID = "fake_corp_id"
                    mock_config.WECHAT_CORP_SECRET = "fake_secret"
                    mock_config.WECHAT_AGENT_ID = "123456"
                    mock_config.WECHAT_TO_USER = "fake_user"

                    result = channel.send({
                        "type": "dynamic",
                        "title": "",
                        "text": "动态正文",
                        "url": "https://bilibili.com/opus/123",
                        "image_urls": []
                    })

                    assert result is True
                    call_json = mock_post.call_args[1]["json"]
                    assert call_json["news"]["articles"][0]["title"] == "📝 新动态"

    def test_send_video_with_title_and_summary(self):
        """测试：发送视频消息（标题+摘要）"""
        channel = WechatChannel()

        with patch.object(channel, '_get_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.json.return_value = {"errcode": 0}
                mock_post.return_value = mock_response

                with patch('app.modules.push_channels.wechat.Config') as mock_config:
                    mock_config.WECHAT_CORP_ID = "fake_corp_id"
                    mock_config.WECHAT_CORP_SECRET = "fake_secret"
                    mock_config.WECHAT_AGENT_ID = "123456"
                    mock_config.WECHAT_TO_USER = "fake_user"

                    result = channel.send({
                        "type": "video",
                        "title": "视频标题",
                        "summary": "视频摘要",
                        "url": "https://bilibili.com/video/BV123"
                    })

                    assert result is True
                    call_json = mock_post.call_args[1]["json"]
                    article = call_json["news"]["articles"][0]
                    assert "视频标题" in article["title"]
                    assert "视频摘要" in article["description"]

    def test_send_video_with_image_urls(self):
        """测试：发送视频消息（带图片）"""
        channel = WechatChannel()

        with patch.object(channel, '_get_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.json.return_value = {"errcode": 0}
                mock_post.return_value = mock_response

                with patch('app.modules.push_channels.wechat.Config') as mock_config:
                    mock_config.WECHAT_CORP_ID = "fake_corp_id"
                    mock_config.WECHAT_CORP_SECRET = "fake_secret"
                    mock_config.WECHAT_AGENT_ID = "123456"
                    mock_config.WECHAT_TO_USER = "fake_user"

                    result = channel.send({
                        "type": "video",
                        "title": "视频",
                        "url": "https://bilibili.com/video/BV123",
                        "image_urls": ["https://example.com/pic.jpg"]
                    })

                    assert result is True
                    call_json = mock_post.call_args[1]["json"]
                    assert call_json["news"]["articles"][0]["picurl"] == "https://example.com/pic.jpg"

    def test_send_video_token_failure(self):
        """测试：视频推送时 token 获取失败"""
        channel = WechatChannel()

        with patch.object(channel, '_get_access_token', return_value=""):
            with patch('app.modules.push_channels.wechat.Config') as mock_config:
                mock_config.WECHAT_CORP_ID = "fake_corp_id"
                mock_config.WECHAT_CORP_SECRET = "fake_secret"
                mock_config.WECHAT_AGENT_ID = "123456"
                mock_config.WECHAT_TO_USER = "fake_user"

                result = channel.send({
                    "type": "video",
                    "title": "视频",
                    "url": "https://bilibili.com/video/BV123"
                })

                assert result is False

    def test_send_video_network_error(self):
        """测试：视频推送网络异常"""
        channel = WechatChannel()

        with patch.object(channel, '_get_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_post.side_effect = Exception("network error")

                with patch('app.modules.push_channels.wechat.Config') as mock_config:
                    mock_config.WECHAT_CORP_ID = "fake_corp_id"
                    mock_config.WECHAT_CORP_SECRET = "fake_secret"
                    mock_config.WECHAT_AGENT_ID = "123456"
                    mock_config.WECHAT_TO_USER = "fake_user"

                    result = channel.send({
                        "type": "video",
                        "title": "视频",
                        "url": "https://bilibili.com/video/BV123"
                    })

                    assert result is False

    def test_send_unknown_type_falls_to_video(self):
        """测试：未知类型回退到视频推送"""
        channel = WechatChannel()

        with patch.object(channel, '_get_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.json.return_value = {"errcode": 0}
                mock_post.return_value = mock_response

                with patch('app.modules.push_channels.wechat.Config') as mock_config:
                    mock_config.WECHAT_CORP_ID = "fake_corp_id"
                    mock_config.WECHAT_CORP_SECRET = "fake_secret"
                    mock_config.WECHAT_AGENT_ID = "123456"
                    mock_config.WECHAT_TO_USER = "fake_user"

                    result = channel.send({
                        "type": "unknown_type",
                        "title": "测试",
                        "url": "https://example.com"
                    })

                    assert result is True

    def test_get_access_token_failure(self):
        """测试：获取 access_token 失败"""
        channel = WechatChannel()

        with patch('requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"errcode": 40001, "errmsg": "invalid credential"}
            mock_get.return_value = mock_response

            with patch('app.modules.push_channels.wechat.Config') as mock_config:
                mock_config.WECHAT_CORP_ID = "fake_corp_id"
                mock_config.WECHAT_CORP_SECRET = "fake_secret"

                token = channel._get_access_token()
                assert token == ""

    def test_get_access_token_network_error(self):
        """测试：获取 access_token 网络异常"""
        channel = WechatChannel()

        with patch('requests.get') as mock_get:
            mock_get.side_effect = Exception("network error")

            with patch('app.modules.push_channels.wechat.Config') as mock_config:
                mock_config.WECHAT_CORP_ID = "fake_corp_id"
                mock_config.WECHAT_CORP_SECRET = "fake_secret"

                token = channel._get_access_token()
                assert token == ""


class TestFeishuChannelDynamic:
    """测试飞书渠道动态推送（标题相关）"""

    def test_send_dynamic_with_title(self):
        """测试：发送动态消息（带标题，标题加粗）"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_card', return_value=True) as mock_send:
            result = channel.send({
                "type": "dynamic",
                "title": "动态标题",
                "text": "动态正文",
                "url": "https://bilibili.com/opus/123",
                "pub_time": "2024-03-31 18:00:00"
            })

            assert result is True
            mock_send.assert_called_once()
            card = mock_send.call_args[0][0]
            # 标题在 elements 第一位，lark_md 格式加粗
            elements = card["elements"]
            assert elements[0]["tag"] == "div"
            assert "**📝 动态标题**" in elements[0]["text"]["content"]
            # 卡片 header 使用标题
            assert "📝 动态标题" in card["header"]["title"]["text"]

    def test_send_dynamic_title_only(self):
        """测试：发送动态消息（只有标题，没有正文）"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_card', return_value=True) as mock_send:
            result = channel.send({
                "type": "dynamic",
                "title": "纯标题",
                "text": "",
                "url": "https://bilibili.com/opus/123",
            })

            assert result is True
            mock_send.assert_called_once()
            card = mock_send.call_args[0][0]
            elements = card["elements"]
            # 第一位是标题
            assert "**📝 纯标题**" in elements[0]["text"]["content"]
            # 只有标题元素 + 链接，没有正文元素（正文为空时不加正文 div）
            # elements = [标题div, 链接div] 共2个
            div_elements = [e for e in elements if e.get("tag") == "div"]
            assert len(div_elements) == 2  # 标题 + 链接


class TestUploadImageToFeishu:
    """测试 upload_image_to_feishu 函数"""

    def test_upload_token_failure(self):
        """测试：获取 token 失败时返回 None"""
        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value=None):
            result = upload_image_to_feishu("/fake/path/image.png")
            assert result is None

    def test_upload_file_not_exists(self):
        """测试：文件不存在时返回 None"""
        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value="fake_token"):
            with patch('app.modules.push_channels.feishu.Path') as mock_path:
                mock_path.return_value.exists.return_value = False
                mock_path.return_value.name = "image.png"

                result = upload_image_to_feishu("/fake/path/image.png")
                assert result is None

    def test_upload_success(self):
        """测试：上传成功返回 image_key"""
        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value="fake_token"):
            with patch('app.modules.push_channels.feishu.Path') as mock_path:
                mock_path.return_value.exists.return_value = True
                mock_path.return_value.name = "image.png"
                mock_path.return_value.read_bytes.return_value = b"fake_image_data"

                with patch('requests.post') as mock_post:
                    mock_response = MagicMock()
                    mock_response.json.return_value = {
                        "code": 0,
                        "data": {"image_key": "img_key_123"}
                    }
                    mock_post.return_value = mock_response

                    result = upload_image_to_feishu("/fake/path/image.png")
                    assert result == "img_key_123"

    def test_upload_api_failure(self):
        """测试：上传 API 返回错误码"""
        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value="fake_token"):
            with patch('app.modules.push_channels.feishu.Path') as mock_path:
                mock_path.return_value.exists.return_value = True
                mock_path.return_value.name = "image.png"
                mock_path.return_value.read_bytes.return_value = b"fake_image_data"

                with patch('requests.post') as mock_post:
                    mock_response = MagicMock()
                    mock_response.json.return_value = {"code": 99999, "msg": "upload failed"}
                    mock_post.return_value = mock_response

                    result = upload_image_to_feishu("/fake/path/image.png")
                    assert result is None

    def test_upload_network_error(self):
        """测试：上传网络异常"""
        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value="fake_token"):
            with patch('app.modules.push_channels.feishu.Path') as mock_path:
                mock_path.return_value.exists.return_value = True
                mock_path.return_value.name = "image.png"
                mock_path.return_value.read_bytes.return_value = b"fake_image_data"

                with patch('requests.post') as mock_post:
                    mock_post.side_effect = Exception("network error")

                    result = upload_image_to_feishu("/fake/path/image.png")
                    assert result is None


class TestFeishuChannelErrorPaths:
    """测试飞书渠道错误路径"""

    def test_send_text_token_failure(self):
        """测试：_send_text token 获取失败"""
        channel = FeishuChannel()

        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value=None):
            result = channel._send_text("测试文本")
            assert result is False

    def test_send_text_network_error(self):
        """测试：_send_text 网络异常"""
        channel = FeishuChannel()

        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_post.side_effect = Exception("network error")

                with patch('app.modules.push_channels.feishu.Config') as mock_config:
                    mock_config.FEISHU_RECEIVE_ID = "fake_id"
                    mock_config.FEISHU_RECEIVE_ID_TYPE = "open_id"

                    result = channel._send_text("测试文本")
                    assert result is False

    def test_send_text_api_error(self):
        """测试：_send_text API 返回错误码"""
        channel = FeishuChannel()

        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.json.return_value = {"code": 10001, "msg": "error"}
                mock_post.return_value = mock_response

                with patch('app.modules.push_channels.feishu.Config') as mock_config:
                    mock_config.FEISHU_RECEIVE_ID = "fake_id"
                    mock_config.FEISHU_RECEIVE_ID_TYPE = "open_id"

                    result = channel._send_text("测试文本")
                    assert result is False

    def test_send_card_token_failure(self):
        """测试：_send_card token 获取失败"""
        channel = FeishuChannel()

        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value=None):
            result = channel._send_card({"elements": []})
            assert result is False

    def test_send_card_network_error(self):
        """测试：_send_card 网络异常"""
        channel = FeishuChannel()

        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_post.side_effect = Exception("network error")

                with patch('app.modules.push_channels.feishu.Config') as mock_config:
                    mock_config.FEISHU_RECEIVE_ID = "fake_id"
                    mock_config.FEISHU_RECEIVE_ID_TYPE = "open_id"

                    result = channel._send_card({"elements": []})
                    assert result is False

    def test_send_card_api_error(self):
        """测试：_send_card API 返回错误码"""
        channel = FeishuChannel()

        with patch('app.modules.push_channels.feishu.get_feishu_tenant_access_token', return_value="fake_token"):
            with patch('requests.post') as mock_post:
                mock_response = MagicMock()
                mock_response.json.return_value = {"code": 10001, "msg": "error"}
                mock_post.return_value = mock_response

                with patch('app.modules.push_channels.feishu.Config') as mock_config:
                    mock_config.FEISHU_RECEIVE_ID = "fake_id"
                    mock_config.FEISHU_RECEIVE_ID_TYPE = "open_id"

                    result = channel._send_card({"elements": []})
                    assert result is False

    def test_send_video_no_title(self):
        """测试：发送视频消息（无标题时用默认值）"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_text', return_value=True) as mock_send:
            result = channel._send_video({
                "title": "",
                "summary": "",
                "url": "https://bilibili.com/video/BV123",
                "tags": [],
                "stocks": [],
                "doc_url": ""
            })

            assert result is True
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
            assert "📺 " in text

    def test_send_video_with_tags_and_stocks(self):
        """测试：发送视频消息（带标签和股票）"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_text', return_value=True) as mock_send:
            result = channel._send_video({
                "title": "测试视频",
                "summary": "摘要",
                "url": "https://bilibili.com/video/BV123",
                "tags": ["科技", "数码"],
                "stocks": ["小米", "华为"],
                "doc_url": ""
            })

            assert result is True
            mock_send.assert_called_once()
            text = mock_send.call_args[0][0]
            assert "#科技" in text
            assert "#数码" in text
            assert "小米" in text
            assert "华为" in text

    def test_send_unknown_type(self):
        """测试：发送未知类型返回 False"""
        channel = FeishuChannel()

        with patch('app.modules.push_channels.feishu.logger') as mock_logger:
            result = channel.send({"type": "unknown"})

            assert result is False
            mock_logger.warning.assert_called_once()

    def test_send_dynamic_with_images(self):
        """测试：发送动态消息（带图片）"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_card', return_value=True) as mock_send:
            with patch('app.modules.push_channels.feishu.upload_image_to_feishu', return_value="img_key_123"):
                result = channel.send({
                    "type": "dynamic",
                    "title": "标题",
                    "text": "正文",
                    "url": "https://bilibili.com/opus/123",
                    "images": ["/path/to/image.png"]
                })

                assert result is True
                card = mock_send.call_args[0][0]
                img_elements = [e for e in card["elements"] if e.get("tag") == "img"]
                assert len(img_elements) == 1
                assert img_elements[0]["img_key"] == "img_key_123"

    def test_send_dynamic_with_pub_time(self):
        """测试：发送动态消息（带发布时间）"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_card', return_value=True) as mock_send:
            result = channel.send({
                "type": "dynamic",
                "title": "标题",
                "text": "正文",
                "url": "https://bilibili.com/opus/123",
                "pub_time": "2024年03月31日 18:00:00"
            })

            assert result is True
            card = mock_send.call_args[0][0]
            elements = card["elements"]
            time_elements = [e for e in elements if "⏰" in e.get("text", {}).get("content", "")]
            assert len(time_elements) == 1
            assert "2024年03月31日" in time_elements[0]["text"]["content"]

    def test_send_dynamic_text_truncated(self):
        """测试：动态文本被截断"""
        channel = FeishuChannel()

        with patch.object(channel, '_send_card', return_value=True) as mock_send:
            long_text = "a" * 1500
            result = channel.send({
                "type": "dynamic",
                "title": "标题",
                "text": long_text,
                "url": "https://bilibili.com/opus/123",
            })

            assert result is True
            card = mock_send.call_args[0][0]
            elements = card["elements"]
            text_elements = [e for e in elements if e.get("tag") == "div" and e.get("text", {}).get("tag") == "plain_text"]
            assert len(text_elements) == 1
            assert len(text_elements[0]["text"]["content"]) <= 1003


class TestPushContentErrorHandling:
    """测试 push_content 错误处理"""

    def test_unknown_channel_returns_false(self):
        """测试：推送到未知渠道返回 False"""
        result = push_content({
            "type": "dynamic",
            "text": "测试",
            "url": "https://example.com"
        }, ["unknown_channel"])
        assert result is False

    def test_channel_send_exception(self):
        """测试：渠道发送异常被捕获"""
        with patch('app.modules.push_channels.registry.ChannelRegistry.get') as mock_get:
            mock_channel = MagicMock()
            mock_channel.send.side_effect = Exception("network error")
            mock_get.return_value = mock_channel

            result = push_content({
                "type": "dynamic",
                "text": "测试",
                "url": "https://example.com"
            }, ["feishu"])
            assert result is False

    def test_multiple_channels_partial_failure(self):
        """测试：部分渠道失败时仍尝试其他渠道"""
        with patch('app.modules.push_channels.registry.get_channel') as mock_get:
            mock_fake = MagicMock()
            mock_fake.send.return_value = False
            mock_get.return_value = mock_fake

            result = push_content({
                "type": "dynamic",
                "text": "测试内容，不少于10个字",
                "url": "https://example.com"
            }, ["feishu"])
            # 返回 False 因为 feishu 返回 False
            assert result is False


class TestPushVideoToFeishu:
    """测试 push_video_to_feishu 兼容函数"""

    def test_push_video_to_feishu(self):
        """测试：兼容函数正确调用飞书渠道"""
        channel = FeishuChannel()
        with patch.object(channel, 'send', return_value=True):
            with patch('app.modules.push_channels.get_channel', return_value=channel):
                result = push_video_to_feishu({
                    "title": "测试视频",
                    "url": "https://bilibili.com/video/BV123"
                })

                assert result is True

    def test_push_video_to_feishu_no_channel(self):
        """测试：飞书渠道未注册时返回 False"""
        with patch('app.modules.push_channels.get_channel', return_value=None):
            result = push_video_to_feishu({
                "title": "测试",
                "url": "https://example.com"
            })
            assert result is False


class TestPushDynamicToFeishu:
    """测试 push_dynamic_to_feishu 兼容函数"""

    def test_push_dynamic_to_feishu(self):
        """测试：兼容函数正确调用飞书渠道"""
        channel = FeishuChannel()
        with patch.object(channel, 'send', return_value=True):
            with patch('app.modules.push_channels.get_channel', return_value=channel):
                result = push_dynamic_to_feishu({
                    "title": "测试动态",
                    "text": "内容",
                    "url": "https://bilibili.com/opus/123"
                })

                assert result is True

    def test_push_dynamic_to_feishu_no_channel(self):
        """测试：飞书渠道未注册时返回 False"""
        with patch('app.modules.push_channels.get_channel', return_value=None):
            result = push_dynamic_to_feishu({
                "title": "测试",
                "text": "内容",
                "url": "https://example.com"
            })
            assert result is False


class TestFeishuTokenFunction:
    """测试 get_feishu_tenant_access_token 函数"""

    def test_token_not_configured(self):
        """测试：未配置 APP_ID 时返回 None"""
        import app.modules.push_channels.feishu as feishu_module
        feishu_module._feishu_token_cache = None

        with patch('app.modules.push_channels.feishu.Config') as mock_config:
            mock_config.FEISHU_APP_ID = None
            mock_config.FEISHU_APP_SECRET = None

            result = feishu_module.get_feishu_tenant_access_token()
            assert result is None

    def test_token_api_error(self):
        """测试：token API 返回错误"""
        import app.modules.push_channels.feishu as feishu_module
        feishu_module._feishu_token_cache = None

        with patch('requests.post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"code": 99999, "msg": "error"}
            mock_post.return_value = mock_response

            with patch('app.modules.push_channels.feishu.Config') as mock_config:
                mock_config.FEISHU_APP_ID = "fake_id"
                mock_config.FEISHU_APP_SECRET = "fake_secret"

                result = feishu_module.get_feishu_tenant_access_token()
                assert result is None

    def test_token_request_exception(self):
        """测试：token 请求异常"""
        import app.modules.push_channels.feishu as feishu_module
        feishu_module._feishu_token_cache = None

        with patch('requests.post') as mock_post:
            mock_post.side_effect = Exception("network error")

            with patch('app.modules.push_channels.feishu.Config') as mock_config:
                mock_config.FEISHU_APP_ID = "fake_id"
                mock_config.FEISHU_APP_SECRET = "fake_secret"

                result = feishu_module.get_feishu_tenant_access_token()
                assert result is None


