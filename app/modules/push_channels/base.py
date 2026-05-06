from abc import ABC, abstractmethod
from typing import Dict, Any, List


class BaseChannel(ABC):
    """推送渠道基类"""

    channel_name: str = ""

    @abstractmethod
    def send(self, content_data: Dict[str, Any]) -> bool:
        """
        发送内容

        Args:
            content_data: 内容数据
                - type: "video" | "dynamic"
                - title/text: 标题或文本
                - summary: 摘要（仅视频）
                - url: 链接
                - images: 本地图片路径列表
                - image_urls: 图片URL列表
                - pub_time: 发布时间字符串
                - tags: 标签列表
                - stocks: 股票列表
                - uploader_name: UP主名字（可选）

        Returns:
            bool: 是否发送成功
        """
        pass

    def send_text(self, text: str) -> bool:
        """发送纯文本（可选实现）"""
        raise NotImplementedError

    def batch_send(self, content_list: List[Dict[str, Any]]) -> bool:
        """批量发送（可选实现）"""
        raise NotImplementedError
