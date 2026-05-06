import time
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import wraps
from app.utils.logger import get_logger
from app.models.database import get_db, Video, Dynamic, Subscription, ClassificationRule
from app.modules.subtitle import get_subtitles
from app.modules.whisper_ai import transcribe_audio
from app.modules.processor import process_text
from app.modules.push import push_content, get_enabled_channels
from app.modules.dynamic import should_push_dynamic
from app.utils.paths import get_path_manager

logger = get_logger("queue_worker")


def retry_on_db_lock(max_retries=3, delay=0.5):
    """数据库锁定重试装饰器"""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        logger.warning("数据库锁定，第%d次重试: %s", attempt + 1, e)
                        time.sleep(delay * (attempt + 1))
                    else:
                        raise

        return wrapper

    return decorator


# 数据保存路径（旧路径，用于向后兼容）
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
TEXT_DIR = DATA_ROOT / "text"
MARKDOWN_DIR = DATA_ROOT / "markdown"

# 确保目录存在
TEXT_DIR.mkdir(parents=True, exist_ok=True)
MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)


def get_uploader_info(db, mid: str) -> tuple:
    """获取 UP 主信息"""
    sub = db.query(Subscription).filter_by(mid=mid).first()
    if sub:
        return sub.name, sub.mid
    return f"UP主_{mid}", mid


def process_single_video(bvid: str):
    """处理单个视频的完整流程（使用新路径结构）"""
    db = get_db()
    pm = get_path_manager()
    project_root = Path(__file__).resolve().parent.parent

    try:
        video = db.query(Video).filter_by(bvid=bvid).first()
        if not video:
            logger.warning("视频不存在: %s", bvid)
            return

        # 获取 UP 主信息
        uploader_name, uploader_mid = get_uploader_info(db, video.mid)

        logger.info(
            "开始处理视频 %s | UP主: %s | 标题: %s", bvid, uploader_name, video.title
        )
        video.status = "processing"
        db.commit()

        # 获取新路径结构
        paths = pm.get_video_paths(
            uploader_name, bvid, video.title, video.pub_time, uploader_mid
        )

        # 第0步：检查是否已有识别后的文本（优先检查新路径）
        subtitles = ""
        if paths["transcript"].exists():
            logger.debug("[文本] 发现已有识别文本（新路径）: %s", paths["transcript"])
            subtitles = paths["transcript"].read_text("utf-8")
        else:
            # 检查旧路径
            old_text_file = TEXT_DIR / f"{bvid}.txt"
            if old_text_file.exists():
                logger.debug("[文本] 发现已有识别文本（旧路径）: %s", old_text_file)
                subtitles = old_text_file.read_text("utf-8")
                # 复制到新路径
                paths["transcript"].write_text(subtitles, "utf-8")
                logger.debug("[文本] 已迁移到新路径: %s", paths["transcript"])

        if not subtitles:
            # 第1步：获取字幕
            logger.debug("[字幕] 尝试从B站获取...")
            subtitles = get_subtitles(bvid)
            video.has_subtitle = bool(subtitles)

            if subtitles:
                logger.debug("[字幕] 获取成功，长度: %d", len(subtitles))
            else:
                # 第2步：检查是否已下载过视频或音频（优先检查新路径）
                media_path = None
                media_type = None

                # 先检查新路径下的视频
                if video.has_video and paths["video"].exists():
                    logger.debug(
                        "[视频] 复用已有视频文件（新路径）: %s", paths["video"]
                    )
                    media_path = str(paths["video"])
                    media_type = "video"
                # 再检查新路径下的音频
                elif video.has_audio and paths["audio"].exists():
                    logger.debug(
                        "[音频] 复用已有音频文件（新路径）: %s", paths["audio"]
                    )
                    media_path = str(paths["audio"])
                    media_type = "audio"
                else:
                    # 回退到旧路径检查
                    if video.has_video and video.video_path:
                        check_path = video.video_path
                        if not os.path.isabs(check_path):
                            check_path = str(project_root / check_path)
                        if os.path.exists(check_path):
                            logger.debug(
                                "[视频] 复用已有视频文件（旧路径）: %s", check_path
                            )
                            media_path = check_path
                            media_type = "video"
                            # 复制到新路径
                            import shutil

                            shutil.copy2(check_path, paths["video"])
                            video.video_path = str(
                                paths["video"].relative_to(project_root)
                            )
                            logger.debug("[视频] 已迁移到新路径: %s", paths["video"])

                    if not media_path and video.has_audio and video.audio_path:
                        check_path = video.audio_path
                        if not os.path.isabs(check_path):
                            check_path = str(project_root / check_path)
                        if os.path.exists(check_path):
                            logger.debug(
                                "[音频] 复用已有音频文件（旧路径）: %s", check_path
                            )
                            media_path = check_path
                            media_type = "audio"
                            # 复制到新路径
                            import shutil

                            shutil.copy2(check_path, paths["audio"])
                            video.audio_path = str(
                                paths["audio"].relative_to(project_root)
                            )
                            logger.debug("[音频] 已迁移到新路径: %s", paths["audio"])

                # 如果都没有，下载音频
                if not media_path:
                    logger.info("[媒体] 未找到视频或音频文件，开始下载音频...")
                    try:
                        from app.modules.downloader import download_audio_new

                        audio_file = download_audio_new(
                            bvid=bvid,
                            mid=video.mid,
                            title=video.title,
                            pub_time=video.pub_time,
                            uploader_name=uploader_name,
                        )
                        if audio_file and Path(audio_file).exists():
                            media_path = audio_file
                            media_type = "audio"
                            video.has_audio = True
                            video.audio_path = str(
                                Path(audio_file).relative_to(project_root)
                            )
                            logger.info("[音频] 下载成功: %s", audio_file)
                        else:
                            logger.warning("[音频] 下载失败或文件不存在")
                            subtitles = ""
                            media_path = None
                    except Exception as e:
                        logger.error("[音频] 下载失败: %s", e)
                        subtitles = ""
                        media_path = None

                # 用ASR转写
                if media_path:
                    logger.debug("[%s] 开始识别...", media_type)
                    try:
                        subtitles = transcribe_audio(media_path)
                        logger.debug(
                            "[%s] 识别完成，长度: %d", media_type, len(subtitles)
                        )
                    except Exception as e:
                        logger.error("[%s] 识别失败: %s", media_type, e)
                        subtitles = ""

        # 第3步：统一 LLM 处理（纠错 + 总结）
        summary_data = None
        if subtitles:
            logger.debug("[LLM] 开始统一处理（纠错+总结）...")

            # 获取 per-uploader prompt 模板
            custom_prompt = None
            try:
                from app.models.database import SessionLocal

                session = SessionLocal()
                try:
                    sub = session.query(Subscription).filter_by(mid=video.mid).first()
                    uploader_name_for_prompt = sub.name if sub else None
                    if uploader_name_for_prompt:
                        rule = (
                            session.query(ClassificationRule)
                            .filter_by(uploader_name=uploader_name_for_prompt)
                            .first()
                        )
                        pt = rule.prompt_template if rule else None
                        # 空字符串当作 None 处理
                        custom_prompt = pt if (pt is not None and pt != "") else None
                finally:
                    session.close()
            except Exception as e:
                logger.warning("[LLM] 获取 per-uploader prompt 失败: %s", e)
                custom_prompt = None

            process_result = process_text(
                raw_text=subtitles,
                video_title=video.title,
                duration=0,
                custom_prompt=custom_prompt,
            )
            summary_data = {
                "summary": process_result.get("summary", ""),
                "details": process_result.get("details", ""),
                "key_points": process_result.get("key_points", []),
                "tags": process_result.get("tags", []),
                "stocks": process_result.get("stocks", []),
                "insights": process_result.get("insights", ""),
                "duration_minutes": 0,
            }
            video.summary_json = json.dumps(summary_data, ensure_ascii=False)
            logger.debug("[LLM] 处理完成")

            # 保存文本到新路径
            if subtitles and not paths["transcript"].exists():
                paths["transcript"].write_text(subtitles, "utf-8")
                logger.debug("[保存] 文本已保存: %s", paths["transcript"])

            # 保存 summary 到新路径
            md_content = f"# {video.title}\n\n"
            md_content += f"**URL**: https://www.bilibili.com/video/{bvid}\n\n"
            md_content += f"**UP主**: {uploader_name}\n\n"
            if video.pub_time:
                pub_time_str = datetime.fromtimestamp(video.pub_time).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                md_content += f"**发布时间**: {pub_time_str}\n\n"
            else:
                md_content += "**发布时间**: 未知\n\n"
            md_content += "---\n\n"
            md_content += summary_data["details"]

            if not paths["summary"].exists():
                paths["summary"].write_text(md_content, "utf-8")
                logger.debug("[保存] 详情已保存: %s", paths["summary"])

            # 上传到飞书文档
            doc_url = None
            try:
                from app.modules.feishu_docs import push_video_summary_to_doc

                doc_result = push_video_summary_to_doc(
                    title=video.title,
                    markdown_content=md_content,
                    bvid=bvid,
                    pub_time=video.pub_time,
                    uploader_name=uploader_name,
                )
                if doc_result:
                    doc_url = doc_result.get("url")
                    video.doc_url = doc_url
                    logger.info("[飞书文档] 创建成功: %s", doc_url)
            except Exception as e:
                logger.warning("[飞书文档] 创建失败: %s", e)

        else:
            logger.warning("[LLM] 无字幕和音频，跳过处理")
            summary_data = {
                "summary": f"无法获取字幕或音频: {video.title}",
                "details": "",
                "key_points": [],
                "tags": [],
                "stocks": [],
                "insights": "",
                "duration_minutes": 0,
            }
            video.summary_json = json.dumps(summary_data, ensure_ascii=False)
            doc_url = None

        # 第4步：推送
        logger.debug("[推送] 开始推送...")
        push_content(
            {
                "type": "video",
                "title": video.title,
                "uploader_name": uploader_name,
                "summary": summary_data.get("summary", ""),
                "details": summary_data.get("details", ""),
                "key_points": summary_data.get("key_points", []),
                "tags": summary_data.get("tags", []),
                "stocks": summary_data.get("stocks", []),
                "insights": summary_data.get("insights", ""),
                "url": f"https://www.bilibili.com/video/{bvid}",
                "doc_url": doc_url,
                "duration_minutes": summary_data.get("duration_minutes", 0),
                "timestamp": video.pub_time,
            },
            get_enabled_channels(),
        )

        video.status = "done"
        db.commit()
        logger.info("✅ 处理完成: %s", bvid)

    except Exception as e:
        logger.error("❌ 处理失败 %s: %s", bvid, e, exc_info=True)
        try:
            # 重新获取 video 对象，避免 session 问题
            video = db.query(Video).filter_by(bvid=bvid).first()
            if video:
                video.status = "failed"
                video.last_error = str(e)[:200]
                video.attempt_count += 1

                if video.attempt_count >= 3:
                    logger.error("放弃重试: %s (已尝试3次)", bvid)
                else:
                    logger.info(
                        "将重新入队: %s (第%d次重试)", bvid, video.attempt_count
                    )

                db.commit()
        except Exception as db_err:
            logger.error("更新视频状态失败: %s", db_err)
    finally:
        db.close()


def process_single_dynamic(dynamic_id: str):
    """处理单个动态的完整流程"""
    db = get_db()
    try:
        dynamic = db.query(Dynamic).filter_by(dynamic_id=dynamic_id).first()
        if not dynamic:
            logger.warning("动态不存在: %s", dynamic_id)
            return

        logger.info("开始处理动态 %s | 内容: %s...", dynamic_id, dynamic.text[:50])
        dynamic.status = "processing"
        db.commit()

        # 预过滤
        if not should_push_dynamic({"text": dynamic.text}):
            logger.info("动态不符合推送条件: %s", dynamic_id)
            dynamic.status = "filtered"
            dynamic.last_error = "预过滤过滤不符合"
            db.commit()
            return

        # 准备推送数据
        image_paths = (
            json.loads(dynamic.images_path or "[]") if dynamic.images_path else []
        )
        image_urls = (
            json.loads(dynamic.image_urls or "[]") if dynamic.image_urls else []
        )

        logger.debug(
            "[动态数据] 文本: %d字, 图片: %d张",
            len(dynamic.text or ""),
            len(image_paths),
        )

        # 推送
        push_content(
            {
                "type": "dynamic",
                "title": dynamic.title or "",
                "text": dynamic.text,
                "images": image_paths,
                "image_urls": image_urls,
                "pub_time": str(dynamic.pub_time) if dynamic.pub_time else "",
                "url": f"https://www.bilibili.com/opus/{dynamic.dynamic_id}",
            },
            get_enabled_channels(),
        )

        dynamic.status = "sent"
        dynamic.pushed_at = datetime.utcnow()
        db.commit()
        logger.info("✅ 动态推送完成: %s", dynamic_id)

    except Exception as e:
        logger.error("❌ 动态处理失败 %s: %s", dynamic_id, e, exc_info=True)
        try:
            dynamic = db.query(Dynamic).filter_by(dynamic_id=dynamic_id).first()
            if dynamic:
                dynamic.status = "failed"
                dynamic.last_error = str(e)[:200]
                dynamic.attempt_count += 1

                if dynamic.attempt_count >= 3:
                    logger.error("放弃重试: %s (已尝试3次)", dynamic_id)
                else:
                    logger.info(
                        "将重新入队: %s (第%d次重试)", dynamic_id, dynamic.attempt_count
                    )

                db.commit()
        except Exception as db_err:
            logger.error("更新动态状态失败: %s", db_err)
    finally:
        db.close()


def start_queue_worker(max_workers: int = 3):
    """启动队列处理worker，持续处理待处理任务"""
    logger.info("=" * 50)
    logger.info("队列处理线程启动，max_workers=%d", max_workers)
    logger.info("=" * 50)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        loop_count = 0
        while True:
            loop_count += 1

            try:
                db = get_db()
                try:
                    # 优先处理动态（处理快）- 按发布时间升序，先处理最早的
                    pending_dynamics = (
                        db.query(Dynamic)
                        .filter_by(status="pending")
                        .order_by(Dynamic.pub_time.asc().nullslast())
                        .limit(5)
                        .all()
                    )

                    # 然后处理视频
                    pending_videos = (
                        db.query(Video)
                        .filter_by(status="pending")
                        .order_by(Video.created_at)
                        .limit(5)
                        .all()
                    )

                    # 处理已失败但还能重试的任务
                    retry_videos = (
                        db.query(Video)
                        .filter_by(status="failed")
                        .filter(Video.attempt_count < 3)
                        .limit(2)
                        .all()
                    )

                    retry_dynamics = (
                        db.query(Dynamic)
                        .filter_by(status="failed")
                        .filter(Dynamic.attempt_count < 3)
                        .limit(2)
                        .all()
                    )

                    _total_pending = len(pending_dynamics) + len(pending_videos)
                    total_retry = len(retry_videos) + len(retry_dynamics)

                    if loop_count % 6 == 0:  # 每30秒（6个5秒循环）打印一次统计
                        logger.info(
                            "[定期统计] 待处理动态: %d, 待处理视频: %d, 重试队列: %d",
                            len(pending_dynamics),
                            len(pending_videos),
                            total_retry,
                        )

                    if (
                        not pending_dynamics
                        and not pending_videos
                        and not retry_dynamics
                        and not retry_videos
                    ):
                        logger.debug("暂无待处理任务，休眠...")
                        time.sleep(30)
                        continue

                    # 提交动态任务 - 先更新状态为 processing
                    for dyn in pending_dynamics:
                        dyn.status = "processing"
                        retry_on_db_lock()(db.commit)()
                        executor.submit(process_single_dynamic, dyn.dynamic_id)

                    # 提交已失败但可重试的动态 - 先更新状态为 processing
                    for dyn in retry_dynamics:
                        logger.info(
                            "重新处理失败动态: %s (第%d次重试)",
                            dyn.dynamic_id,
                            dyn.attempt_count + 1,
                        )
                        dyn.status = "processing"
                        retry_on_db_lock()(db.commit)()
                        executor.submit(process_single_dynamic, dyn.dynamic_id)

                    # 提交视频任务 - 先更新状态为 processing
                    for vid in pending_videos:
                        vid.status = "processing"
                        retry_on_db_lock()(db.commit)()
                        executor.submit(process_single_video, vid.bvid)

                    # 提交已失败但可重试的视频 - 先更新状态为 processing
                    for vid in retry_videos:
                        logger.info(
                            "重新处理失败视频: %s (第%d次重试)",
                            vid.bvid,
                            vid.attempt_count + 1,
                        )
                        vid.status = "processing"
                        retry_on_db_lock()(db.commit)()
                        executor.submit(process_single_video, vid.bvid)

                    time.sleep(5)

                finally:
                    db.close()

            except Exception as e:
                logger.error("队列处理循环异常: %s", e, exc_info=True)
                time.sleep(10)  # 出错时休眠较长时间
