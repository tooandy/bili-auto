import schedule
import time
import json
import asyncio
import threading
from datetime import datetime
from pathlib import Path
from app.utils.logger import get_logger
from app.models.database import get_db, Subscription, Video, Dynamic
from app.modules.dynamic import DynamicFetcher
from app.modules.bilibili_auth import get_auth_manager
from config import Config

logger = get_logger("scheduler")

# 热加载相关
_env_file_path = Path(".env")
_env_last_mtime = None
_last_cookie = None

# Cookie 错误通知标志（防止重复通知）
_cookie_error_lock = threading.Lock()
_cookie_error_triggered = False


def _get_env_mtime() -> float:
    """获取 .env 文件最后修改时间"""
    if _env_file_path.exists():
        return _env_file_path.stat().st_mtime
    return 0


def _check_and_reload_env():
    """检查并重载 .env 文件（如果已修改）"""
    global _env_last_mtime, _last_cookie

    current_mtime = _get_env_mtime()
    if current_mtime > _env_last_mtime:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=_env_file_path)
        _env_last_mtime = current_mtime

        # 重新读取 .env 中的 Cookie 并更新 Config
        from config import Config
        import os
        new_cookie = os.getenv("BILIBILI_COOKIE", "")
        if new_cookie != _last_cookie:
            Config.BILIBILI_COOKIE = new_cookie
            logger.info("检测到 .env 中 Cookie 变化，已更新 Config")
            logger.info("[文件监控] .env 变化已加载，新 Cookie 将立即生效")
            _last_cookie = new_cookie
        else:
            logger.info(".env 文件已重载（Cookie 未变化）")
        return True
    return False


def _start_env_watcher():
    """启动 .env 文件监控线程（后台运行）"""
    global _env_last_mtime, _last_cookie

    _env_last_mtime = _get_env_mtime()
    _last_cookie = Config.BILIBILI_COOKIE

    def watch():
        logger.info("[文件监控] 启动 .env 监控线程")
        last_check = time.time()
        while True:
            try:
                time.sleep(2)
                current_time = time.time()
                if current_time - last_check < 2:
                    continue
                last_check = current_time

                if _check_and_reload_env():
                    logger.info("[文件监控] .env 变化已加载，下次动态检测时将使用新 Cookie")
            except Exception as e:
                logger.error("[文件监控] 监控异常: %s", e)
                time.sleep(5)

    t = threading.Thread(target=watch, daemon=True, name="env-watcher")
    t.start()
    logger.info("[文件监控] .env 监控线程已启动")
    return t


def check_and_refresh_cookie():
    """
    检查并刷新 Cookie（同步封装）

    Returns:
        如果刷新了返回新的 Cookie，否则返回 None
    """
    if not Config.BILIBILI_COOKIE:
        logger.debug("未配置 BILIBILI_COOKIE，跳过 Cookie 刷新检查")
        return None

    auth = get_auth_manager()
    refresh_token = auth.get_refresh_token()

    if not refresh_token:
        logger.debug("未配置 refresh_token，跳过 Cookie 自动刷新")
        return None

    logger.info("开始检查 Cookie 是否需要刷新...")

    # 创建事件循环来运行异步代码
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        new_cookie, refreshed = loop.run_until_complete(
            auth.auto_refresh_if_needed(Config.BILIBILI_COOKIE)
        )
        if refreshed:
            logger.info("Cookie 已刷新！")
            # 更新 Config 中的 Cookie（当前进程）
            Config.BILIBILI_COOKIE = new_cookie
            return new_cookie
        else:
            logger.debug("Cookie 无需刷新")
            return None
    except Exception as e:
        logger.error(f"Cookie 刷新过程出错: {e}", exc_info=True)
        _notify_cookie_error(str(e))
        return None


def _reset_cookie_error_flag():
    """重置 Cookie 错误标志（自动登录成功后调用）"""
    global _cookie_error_triggered
    with _cookie_error_lock:
        _cookie_error_triggered = False
        logger.debug("[Cookie] 错误标志已重置")


def _notify_cookie_error(error_msg: str, from_runtime: bool = False):
    """发送 Cookie 错误通知

    Args:
        error_msg: 错误信息
        from_runtime: 是否来自运行时检测（True 表示来自 check_new_dynamics）
    """
    global _cookie_error_triggered

    # 检查是否已经触发过（防止重复通知）
    with _cookie_error_lock:
        if _cookie_error_triggered:
            logger.debug("[Cookie] 错误通知已触发，跳过")
            return
        _cookie_error_triggered = True

    try:
        from app.modules.push_channels import get_enabled_channels, push_content

        channels = get_enabled_channels()

        if from_runtime:
            # 运行时检测到的 Cookie 过期，需要启动自动登录
            title = "B站 Cookie 已过期（运行时检测）"
            text = f"检测到 Cookie 失效: {error_msg}\n\n程序将自动发起登录流程，请稍后查看飞书推送的登录链接"
        else:
            title = "B站 Cookie 已过期"
            text = f"Cookie 检查失败: {error_msg}\n\n程序将自动发起登录流程，请稍后查看飞书推送的登录链接"

        push_content({
            "type": "cookie_error",
            "title": title,
            "text": text,
            "url": "",
        }, channels)

        # 启动自动登录线程，登录成功后重置错误标志
        from app.modules.auto_relogin import start_auto_relogin_thread
        start_auto_relogin_thread(on_success=_reset_cookie_error_flag)

    except Exception as notify_err:
        logger.error(f"发送 Cookie 错误通知失败: {notify_err}")
        # 失败时重置标志，允许重试
        with _cookie_error_lock:
            _cookie_error_triggered = False


def check_new_dynamics():
    """定时检测所有UP主的新动态"""
    logger.info("[检测] 开始检查新动态...")

    try:
        db = get_db()
        # 使用上下文管理器确保 Session 正确关闭
        with DynamicFetcher() as fetcher:
            subscriptions = db.query(Subscription).filter_by(is_active=True).all()

            if not subscriptions:
                logger.warning("[检测] 未配置任何UP主订阅")
                return

            # 收集所有新动态
            all_new_dynamics = []
            error_count = 0

            for sub in subscriptions:
                try:
                    dynamics = fetcher.fetch_dynamic(sub.mid)
                    logger.debug("[检测] 用户 %s(%s) 获得 %d 个动态",
                                sub.name, sub.mid, len(dynamics))

                    for dyn in dynamics:
                        # 检查是否已存在
                        existing = db.query(Dynamic).filter_by(
                            dynamic_id=dyn["dynamic_id"]
                        ).first()
                        if existing:
                            logger.debug("[检测] 动态已存在: %s", dyn["dynamic_id"])
                            continue

                        # 下载图片
                        dyn = fetcher.download_images(dyn)
                        dyn["mid"] = sub.mid
                        dyn["sub_name"] = sub.name
                        all_new_dynamics.append(dyn)

                    sub.last_check_time = datetime.utcnow()

                except Exception as e:
                    error_count += 1
                    error_str = str(e)
                    logger.error("[检测] 检查用户 %s(%s) 动态失败: %s",
                               sub.mid, sub.name, e, exc_info=True)

                    # 检测是否是 WBI 密钥获取失败（Cookie 过期）
                    if "WBI 密钥获取失败" in error_str:
                        _notify_cookie_error(error_str, from_runtime=True)

            # 按发布时间排序（最早的在前）
            all_new_dynamics.sort(key=lambda d: d.get("pub_time") or datetime.min)

            # 保存到数据库并立即推送
            from app.modules.push import push_content
            for dyn in all_new_dynamics:
                new_dynamic = Dynamic(
                    dynamic_id=dyn["dynamic_id"],
                    mid=dyn["mid"],
                    type=dyn.get("type", 0),
                    title=dyn.get("title", ""),
                    text=dyn.get("text", ""),
                    image_count=len(dyn.get("images", [])),
                    images_path=json.dumps(dyn.get("images", []), ensure_ascii=False),
                    image_urls=json.dumps(dyn.get("image_urls", []), ensure_ascii=False),
                    pub_time=dyn.get("pub_time"),
                    status="sent",
                    pushed_at=datetime.utcnow(),
                    video_bvid=dyn.get("bvid")
                )
                db.add(new_dynamic)

                # 如果是视频动态，同时创建 Video 记录
                if dyn.get("bvid"):
                    try:
                        existing_video = db.query(Video).filter_by(bvid=dyn["bvid"]).first()
                        if not existing_video:
                            pub_time = dyn.get("pub_ts") or dyn.get("pub_time")
                            new_video = Video(
                                bvid=dyn["bvid"],
                                title=dyn.get("title") or "",
                                mid=dyn.get("mid"),
                                pub_time=pub_time,
                                status="pending"
                            )
                            db.add(new_video)
                            logger.info("[视频动态] %s | %s (%s) → 创建 Video 记录",
                                        dyn.get("sub_name", ""), dyn.get("title", ""), dyn["bvid"])
                        else:
                            logger.info("[视频动态] %s 已存在，跳过", dyn["bvid"])
                    except Exception as e:
                        logger.error("[视频动态] 创建 Video 记录失败: %s", e)

                # 立即推送（按时间顺序）
                pub_time_str = str(dyn["pub_time"]) if dyn.get("pub_time") else ""
                push_content({
                    "type": "dynamic",
                    "uploader_name": dyn.get("sub_name", ""),
                    "title": dyn.get("title", ""),
                    "text": dyn.get("text", ""),
                    "images": dyn.get("images", []),
                    "image_urls": dyn.get("image_urls", []),
                    "pub_time": pub_time_str,
                    "url": f"https://www.bilibili.com/opus/{dyn['dynamic_id']}"
                }, ["feishu"])
                logger.info("[推送] %s | %s...", dyn.get("sub_name", ""), (dyn.get("text", "") or dyn.get("title", ""))[:50])

            db.commit()
            logger.info("[检测完成] 发现 %d 个新动态，%d 个错误", len(all_new_dynamics), error_count)

    except Exception as e:
        logger.error("[检测] 异常: %s", e, exc_info=True)


def start_scheduler():
    """启动定时任务调度"""
    logger.info("=" * 50)
    logger.info("定时任务调度启动")

    # 启动 .env 文件热加载监控
    _start_env_watcher()

    dynamic_interval = Config.DYNAMIC_CHECK_INTERVAL

    # 动态检测
    if dynamic_interval > 0:
        logger.info("动态检测频率: 每%d分钟", dynamic_interval)
        schedule.every(dynamic_interval).minutes.do(check_new_dynamics)
    else:
        logger.info("动态检测: 已禁用 (DYNAMIC_CHECK_INTERVAL=%d)", dynamic_interval)

    logger.info("=" * 50)
    
    loop_count = 0
    while True:
        try:
            loop_count += 1
            schedule.run_pending()
            
            # 每分钟打印一次心跳
            if loop_count % 6 == 0:
                logger.debug("[调度] 心跳正常，已运行 %d 分钟", loop_count // 6 * 10)
            
            time.sleep(10)  # 每10秒检查一次是否有任务需要执行
            
        except Exception as e:
            logger.error("[调度] 异常: %s", e, exc_info=True)
            time.sleep(30)
