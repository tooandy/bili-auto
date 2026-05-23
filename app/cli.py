#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站自动化工具 CLI

使用方法:
    uv run python -m app.cli login
    uv run bili-login
    uv run bili sub list
    uv run bili download BV123456
"""

import io
import json
import sys
import requests
import time
from pathlib import Path
from datetime import datetime
from functools import wraps

import typer

# 确保项目根目录在 sys.path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# noqa: E402 - 需要在 sys.path 设置后才能导入
from app.models.database import get_db, Subscription, Video  # noqa: E402
from app.modules.bilibili import fetch_all_videos  # noqa: E402
from app.modules.downloader import download_video  # noqa: E402
from app.utils.logger import get_logger  # noqa: E402

logger = get_logger("cli")

# 主 CLI
cli = typer.Typer(help="B站自动化工具")

# 子命令组
sub_cli = typer.Typer(help="UP主订阅管理")
download_cli = typer.Typer(help="视频下载")
clear_cli = typer.Typer(help="清理工具")
test_cli = typer.Typer(help="测试工具")

cli.add_typer(sub_cli, name="sub")
cli.add_typer(download_cli, name="download")
cli.add_typer(clear_cli, name="clear")
cli.add_typer(test_cli, name="test")

# 尝试导入 qrcode
try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


# ============================================================================
# 通用工具函数
# ============================================================================

def generate_qr_image(url: str) -> io.BytesIO | None:
    """生成二维码图片"""
    if not HAS_QRCODE:
        return None

    qr = qrcode.QRCode(version=2, box_size=2, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image().convert('RGB')

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return buf


def push_qr_to_feishu(qrcode_key: str, qrcode_url: str) -> bool:
    """推送二维码到飞书"""
    try:
        from app.modules.push_channels.feishu import upload_image_to_feishu, get_feishu_tenant_access_token

        buf = generate_qr_image(qrcode_url)
        if not buf:
            typer.echo("无法生成二维码图片")
            return False

        temp_file = Path("/tmp/bilibili_qrcode.png")
        with open(temp_file, 'wb') as f:
            f.write(buf.getvalue())

        image_key = upload_image_to_feishu(str(temp_file))
        if not image_key:
            typer.echo("飞书图片上传失败")
            return False

        token = get_feishu_tenant_access_token()
        if not token:
            typer.echo("获取飞书token失败")
            return False

        from config import Config
        receive_id = Config.FEISHU_RECEIVE_ID
        receive_id_type = Config.FEISHU_RECEIVE_ID_TYPE

        url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8"
        }

        payload = {
            "receive_id": receive_id,
            "msg_type": "image",
            "content": json.dumps({"image_key": image_key})
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        result = resp.json()

        if result.get("code") == 0:
            text_url = f"https://account.bilibili.com/h5/account-h5/auth/scan-web?qrcode_key={qrcode_key}"
            text_payload = {
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({
                    "text": f"请用 B站 App 扫码登录（点击图片放大后直接扫描）\n\n或者复制链接到浏览器扫码:\n{text_url}"
                })
            }
            requests.post(url, headers=headers, json=text_payload, timeout=15)
            return True
        else:
            typer.echo(f"飞书推送失败: {result.get('msg')}")
            return False
    except Exception as e:
        typer.echo(f"飞书推送异常: {e}")
        return False


def push_qr_to_telegram(qrcode_key: str, qrcode_url: str) -> bool:
    """推送二维码到 Telegram"""
    try:
        import telegram
        from config import Config

        if not Config.TELEGRAM_TOKEN or not Config.TELEGRAM_CHAT_ID:
            return False

        bot = telegram.Bot(token=Config.TELEGRAM_TOKEN)

        buf = generate_qr_image(qrcode_url)
        if not buf:
            return False

        temp_file = Path("/tmp/bilibili_qrcode.png")
        with open(temp_file, 'wb') as f:
            f.write(buf.getvalue())

        with open(temp_file, 'rb') as f:
            bot.send_photo(chat_id=Config.TELEGRAM_CHAT_ID, photo=f,
                          caption=f"请使用 B站 App 扫码登录\n二维码key: {qrcode_key}")
        return True
    except Exception as e:
        typer.echo(f"Telegram推送异常: {e}")
        return False


def save_to_env(refresh_token: str, cookie: str = None) -> bool:
    """保存 refresh_token 和 cookie 到 .env（代理到 login 模块）"""
    from app.modules.login import save_login_to_env as _save
    env_path = str(Path(__file__).parent.parent / ".env")
    return _save(refresh_token, cookie, env_path)


# ============================================================================
# login 命令 - 扫码登录
# ============================================================================

@cli.command()
def login():
    """
    扫码登录 B站，获取 refresh_token 和 Cookie

    二维码会推送到已配置的飞书/Telegram频道，
    也可以直接查看终端中的二维码链接。
    """
    from app.modules.login import generate_qrcode, poll_login_status

    typer.echo("B站扫码登录 - 获取 refresh_token 和 Cookie")
    typer.echo("=" * 60)

    # 生成二维码
    result = generate_qrcode()
    if not result:
        typer.echo("申请二维码失败")
        raise typer.Exit(1)

    qrcode_key, qrcode_url = result

    typer.echo("正在推送二维码到各渠道...")
    typer.echo("=" * 60)

    pushed = []
    if push_qr_to_feishu(qrcode_key, qrcode_url):
        pushed.append("飞书")
        typer.echo("✓ 已推送到飞书")
    if push_qr_to_telegram(qrcode_key, qrcode_url):
        pushed.append("Telegram")
        typer.echo("✓ 已推送到Telegram")

    if not pushed:
        typer.echo("未推送到任何渠道，将显示二维码链接")

    typer.echo(f"\nqrcode_key: {qrcode_key}")
    typer.echo(f"二维码链接: {qrcode_url}")
    typer.echo("请使用 B站 App 扫码登录")
    typer.echo("=" * 60)

    # 轮询登录状态（最多 6 分钟）
    success, refresh_token, cookie = poll_login_status(qrcode_key)

    if success:
        typer.echo("\n✓ 登录成功!")
        typer.echo(f"refresh_token: {refresh_token[:50]}...")

        if cookie:
            typer.echo(f"获取到新 Cookie")
            save_to_env(refresh_token, cookie)
        else:
            save_to_env(refresh_token)

        typer.echo("\n完成！refresh_token 和 Cookie 已保存到 .env")
        raise typer.Exit(0)

    else:
        typer.echo("\n超时，扫码失败")
        raise typer.Exit(1)


# ============================================================================
# sub 命令 - UP主订阅管理
# ============================================================================

@sub_cli.command("list")
def sub_list():
    """显示所有UP主订阅"""
    db = get_db()
    try:
        subs = db.query(Subscription).all()

        if not subs:
            typer.echo("暂无任何UP主订阅")
            return

        typer.echo("\n" + "=" * 80)
        typer.echo(f"{'MID':<15} {'名字':<20} {'状态':<8} {'最后检测':<20}")
        typer.echo("-" * 80)

        for sub in subs:
            status = "激活" if sub.is_active else "禁用"
            last_check = sub.last_check_time.strftime("%Y-%m-%d %H:%M") if sub.last_check_time else "未检测"
            typer.echo(f"{sub.mid:<15} {sub.name:<20} {status:<8} {last_check:<20}")
            if sub.notes:
                typer.echo(f"  备注: {sub.notes}")

        typer.echo(f"\n总计: {len(subs)} 个UP主")
    finally:
        db.close()


@sub_cli.command("add")
def sub_add(mid: str, name: str, notes: str = None):
    """添加单个UP主订阅"""
    db = get_db()
    try:
        existing = db.query(Subscription).filter_by(mid=mid).first()
        if existing:
            typer.echo(f"UP主ID '{mid}' 已存在 (名字: {existing.name})")
            raise typer.Exit(1)

        sub = Subscription(
            mid=mid,
            name=name,
            notes=notes,
            is_active=True
        )
        db.add(sub)
        db.commit()

        typer.echo(f"✓ 成功添加UP主: {name} (MID: {mid})")
    except Exception as e:
        typer.echo(f"添加失败: {e}")
        db.rollback()
        raise typer.Exit(1)
    finally:
        db.close()


@sub_cli.command("add-bulk")
def sub_add_bulk():
    """批量添加UP主订阅（交互式）"""
    typer.echo("批量添加 UP 主订阅")
    typer.echo("格式: mid|名字|备注 (最后一个参数可选)")
    typer.echo("示例: 1988098633|李毓佳|科技UP主")
    typer.echo("按 Ctrl+D 或输入空行结束\n")

    db = get_db()
    added_count = 0
    error_count = 0

    try:
        while True:
            try:
                line = input()
                if not line.strip():
                    break

                parts = line.split('|')
                if len(parts) < 2:
                    typer.echo(f"格式错误: {line} (至少需要 mid|名字)")
                    error_count += 1
                    continue

                mid = parts[0].strip()
                name = parts[1].strip()
                notes = parts[2].strip() if len(parts) > 2 else None

                existing = db.query(Subscription).filter_by(mid=mid).first()
                if existing:
                    typer.echo(f"跳过: '{name}' (MID {mid} 已存在)")
                    error_count += 1
                    continue

                sub = Subscription(
                    mid=mid,
                    name=name,
                    notes=notes,
                    is_active=True
                )
                db.add(sub)
                added_count += 1
                typer.echo(f"✓ {name}")

            except EOFError:
                break
            except Exception as e:
                typer.echo(f"处理错误: {e}")
                error_count += 1

        if added_count > 0:
            db.commit()
            typer.echo(f"\n成功添加 {added_count} 个UP主")

        if error_count > 0:
            typer.echo(f"遇到 {error_count} 个错误")

    finally:
        db.close()


@sub_cli.command("toggle")
def sub_toggle(mid: str):
    """启用/禁用UP主订阅"""
    db = get_db()
    try:
        sub = db.query(Subscription).filter_by(mid=mid).first()

        if not sub:
            typer.echo(f"未找到UP主: {mid}")
            raise typer.Exit(1)

        sub.is_active = not sub.is_active
        db.commit()

        status = "启用" if sub.is_active else "禁用"
        typer.echo(f"{status}: {sub.name}")
    except Exception as e:
        typer.echo(f"操作失败: {e}")
        db.rollback()
        raise typer.Exit(1)
    finally:
        db.close()


@sub_cli.command("delete")
def sub_delete(mid: str):
    """删除UP主订阅"""
    db = get_db()
    try:
        sub = db.query(Subscription).filter_by(mid=mid).first()

        if not sub:
            typer.echo(f"未找到UP主: {mid}")
            raise typer.Exit(1)

        db.delete(sub)
        db.commit()

        typer.echo(f"已删除: {sub.name}")
    except Exception as e:
        typer.echo(f"删除失败: {e}")
        db.rollback()
        raise typer.Exit(1)
    finally:
        db.close()


@sub_cli.command("update")
def sub_update(mid: str, name: str = None, notes: str = None, clear_notes: bool = False):
    """编辑UP主信息"""
    db = get_db()
    try:
        sub = db.query(Subscription).filter_by(mid=mid).first()

        if not sub:
            typer.echo(f"未找到UP主: {mid}")
            raise typer.Exit(1)

        if name:
            sub.name = name
        if clear_notes:
            sub.notes = None
        elif notes:
            sub.notes = notes

        db.commit()
        typer.echo("已更新UP主信息")
    except Exception as e:
        typer.echo(f"更新失败: {e}")
        db.rollback()
        raise typer.Exit(1)
    finally:
        db.close()


# ============================================================================
# download 命令 - 视频下载
# ============================================================================

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
                        logger.warning("数据库锁定，第%d次重试", attempt + 1)
                        time.sleep(delay * (attempt + 1))
                    else:
                        raise
        return wrapper
    return decorator


def safe_commit(db):
    """安全的数据库提交"""
    retry_on_db_lock()(db.commit)()


def get_video_info(bvid: str) -> dict:
    """获取单个视频的详细信息"""
    import requests
    from config import Config

    url = "https://api.bilibili.com/x/web-interface/view"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com",
    }
    if Config.BILIBILI_COOKIE:
        headers["Cookie"] = Config.BILIBILI_COOKIE

    try:
        resp = requests.get(url, params={"bvid": bvid}, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        if data.get("code") != 0:
            logger.error("获取视频信息失败: %s", data.get("message"))
            return None

        view_data = data.get("data", {})
        return {
            "bvid": bvid,
            "title": view_data.get("title"),
            "pubdate": view_data.get("pubdate"),
            "duration": view_data.get("duration"),
            "pic": view_data.get("pic"),
            "description": view_data.get("desc", ""),
            "owner": view_data.get("owner", {}).get("name", ""),
            "mid": view_data.get("owner", {}).get("mid", ""),
        }
    except Exception as e:
        logger.error("获取视频信息异常: %s", e)
        return None


def parse_date(date_str: str) -> int:
    """解析日期字符串 (YYYYMMDD) 为 Unix 时间戳"""
    try:
        dt = datetime.strptime(date_str, "%Y%m%d")
        return int(dt.timestamp())
    except ValueError:
        raise typer.BadParameter(f"无效的日期格式: {date_str}，应为 YYYYMMDD")


@download_cli.command("bv")
def download_bv(
    bvids: list[str] = typer.Argument(..., help="视频 BV 号"),
    quality: str = typer.Option("high", "--quality", "-q",
                                help="视频清晰度: 4k, high, 1080p, 720p, 480p, 360p"),
    force: bool = typer.Option(False, "--force", "-f", help="强制重新下载"),
    skip_queue: bool = typer.Option(False, "--skip-queue", "-s", help="仅下载，不加入处理队列"),
):
    """下载单个或多个视频（BV号）"""
    db = get_db()
    try:
        for i, bvid in enumerate(bvids, 1):
            typer.echo(f"\n[{i}/{len(bvids)}] 处理: {bvid}")
            typer.echo("-" * 50)

            video_info = get_video_info(bvid)
            if not video_info:
                typer.echo(f"❌ 无法获取视频信息: {bvid}")
                continue

            title = video_info["title"]
            pub_time = video_info["pubdate"]
            mid = video_info["mid"]

            typer.echo(f"  标题: {title}")
            typer.echo(f"  UP主: {video_info['owner']}")

            existing = db.query(Video).filter_by(bvid=bvid).first()

            has_video_file = False
            if existing and existing.video_path:
                video_file = Path(existing.video_path)
                has_video_file = video_file.exists()
            if not has_video_file:
                video_dir = Path("data/video")
                if video_dir.exists():
                    for f in video_dir.glob(f"*{bvid}*.mp4"):
                        has_video_file = True
                        break

            if existing:
                if force or not existing.has_video or not has_video_file:
                    if not has_video_file:
                        typer.echo("[续传] 视频文件缺失，重新下载")
                    else:
                        typer.echo("[更新] 强制重新下载")
                    existing.status = "done" if skip_queue else "pending"
                    existing.attempt_count = 0
                    existing.last_error = None
                else:
                    typer.echo("[跳过] 视频已存在且已下载")
                    continue
            else:
                typer.echo("[添加] 新视频")
                new_video = Video(
                    bvid=bvid,
                    title=title,
                    mid=str(mid),
                    pub_time=pub_time,
                    status="done" if skip_queue else "pending"
                )
                db.add(new_video)
                if not skip_queue:
                    safe_commit(db)

            try:
                typer.echo(f"开始下载视频 (清晰度: {quality})...")
                video_path = download_video(
                    bvid,
                    quality=quality,
                    title=title,
                    pub_time=pub_time
                )

                vid_obj = db.query(Video).filter_by(bvid=bvid).first()
                if vid_obj:
                    vid_obj.has_video = True
                    vid_obj.video_path = video_path
                    if skip_queue:
                        vid_obj.status = "done"

                safe_commit(db)
                typer.echo(f"✓ 下载完成: {Path(video_path).name}")

            except Exception as e:
                logger.error("下载失败: %s", e)
                typer.echo(f"❌ 下载失败: {e}")

        typer.echo(f"\n{'='*50}")
        if skip_queue:
            typer.echo("下载完成！视频已跳过处理队列")
        else:
            typer.echo("下载完成！queue_worker 将自动处理视频")
        typer.echo(f"{'='*50}")

    finally:
        db.close()


@download_cli.command("up")
def download_up(
    mid: str = typer.Argument(..., help="UP主 MID"),
    all_videos: bool = typer.Option(True, "--all", help="下载所有视频"),
    start_date: str = typer.Option(None, "--start-date", help="开始日期 (YYYYMMDD)"),
    end_date: str = typer.Option(None, "--end-date", help="结束日期 (YYYYMMDD)"),
    quality: str = typer.Option("high", "--quality", "-q", help="视频清晰度"),
    force: bool = typer.Option(False, "--force", "-f", help="强制重新下载"),
    skip_queue: bool = typer.Option(False, "--skip-queue", "-s", help="仅下载，不加入处理队列"),
):
    """批量下载 UP主视频"""
    start_ts = parse_date(start_date) if start_date else None
    end_ts = parse_date(end_date) if end_date else None

    db = get_db()
    try:
        typer.echo(f"正在获取 UP主 {mid} 的视频列表...")
        videos = fetch_all_videos(mid=mid, start_date=start_ts, end_date=end_ts)

        if not videos:
            typer.echo("未找到符合条件的视频")
            return

        typer.echo(f"找到 {len(videos)} 个视频\n")

        added_count = 0
        updated_count = 0
        skipped_count = 0

        for video in videos:
            bvid = video["bvid"]
            title = video["title"]
            pubdate = video.get("pubdate", 0)

            existing = db.query(Video).filter_by(bvid=bvid).first()

            has_video_file = False
            if existing and existing.video_path:
                video_file = Path(existing.video_path)
                has_video_file = video_file.exists()
            if not has_video_file:
                video_dir = Path("data/video")
                if video_dir.exists():
                    for f in video_dir.glob(f"*{bvid}*.mp4"):
                        has_video_file = True
                        break

            if existing:
                if force or not existing.has_video or not has_video_file:
                    if not has_video_file:
                        typer.echo(f"[续传] {bvid} | 视频文件缺失")
                    else:
                        typer.echo(f"[更新] {bvid} | {title[:50]}...")
                    existing.status = "done" if skip_queue else "pending"
                    existing.attempt_count = 0
                    existing.last_error = None
                    updated_count += 1
                else:
                    typer.echo(f"[跳过] {bvid} | {title[:50]}...")
                    skipped_count += 1
                    continue
            else:
                typer.echo(f"[添加] {bvid} | {title[:50]}...")
                new_video = Video(
                    bvid=bvid,
                    title=title,
                    mid=str(mid),
                    pub_time=pubdate,
                    status="done" if skip_queue else "pending"
                )
                db.add(new_video)
                added_count += 1
                if not skip_queue:
                    safe_commit(db)

            try:
                actual_video_path = download_video(
                    bvid,
                    quality=quality,
                    title=title,
                    pub_time=pubdate
                )

                vid_obj = db.query(Video).filter_by(bvid=bvid).first()
                if vid_obj:
                    vid_obj.has_video = True
                    vid_obj.video_path = actual_video_path
                    if skip_queue:
                        vid_obj.status = "done"

                safe_commit(db)
                typer.echo("  ✓ 下载完成")

            except Exception as e:
                logger.error("下载失败 %s: %s", bvid, e)
                typer.echo(f"  ✗ 下载失败: {e}")

        typer.echo(f"\n{'='*50}")
        typer.echo(f"完成！新增: {added_count}, 更新: {updated_count}, 跳过: {skipped_count}")
        typer.echo(f"{'='*50}")

    finally:
        db.close()


# ============================================================================
# clear 命令 - 清理工具
# ============================================================================

@clear_cli.command("videos")
def clear_videos(mid: str, confirm: bool = typer.Option(False, "--yes", "-y", help="确认删除")):
    """清理指定UP主的所有视频数据"""
    db = get_db()
    try:
        videos = db.query(Video).filter_by(mid=mid).all()

        if not videos:
            typer.echo(f"未找到 UP主 {mid} 的视频")
            return

        typer.echo(f"找到 {len(videos)} 个视频\n")

        video_files = []
        audio_files = []
        text_files = []
        markdown_files = []

        for video in videos:
            bvid = video.bvid

            if video.video_path:
                video_files.append(Path(video.video_path))
            else:
                video_path = Path("data/video") / f"{bvid}.mp4"
                if video_path.exists():
                    video_files.append(video_path)

            if video.audio_path:
                audio_files.append(Path(video.audio_path))
            else:
                audio_path = Path("data/audio") / f"{bvid}.m4a"
                if audio_path.exists():
                    audio_files.append(audio_path)

            text_path = Path("data/text") / f"{bvid}.txt"
            if text_path.exists():
                text_files.append(text_path)

            md_path = Path("data/markdown") / f"{bvid}.md"
            if md_path.exists():
                markdown_files.append(md_path)

        typer.echo("预览：将删除以下内容")
        typer.echo("=" * 60)
        typer.echo(f"数据库记录: {len(videos)} 条")
        typer.echo(f"视频文件: {len(video_files)} 个")
        typer.echo(f"音频文件: {len(audio_files)} 个")
        typer.echo(f"文本文件: {len(text_files)} 个")
        typer.echo(f"Markdown文件: {len(markdown_files)} 个")
        typer.echo()

        typer.echo("视频列表（前5个）:")
        for i, video in enumerate(videos[:5], 1):
            typer.echo(f"  {i}. [{video.bvid}] {video.title}")
        if len(videos) > 5:
            typer.echo(f"  ... 还有 {len(videos) - 5} 个视频")

        if not confirm:
            typer.echo("\n预览模式，使用 --yes 确认删除")
            return

        typer.echo("\n开始删除...")

        deleted_files = 0

        for file_path in video_files:
            try:
                file_path.unlink()
                typer.echo(f"✓ 删除视频: {file_path}")
                deleted_files += 1
            except Exception as e:
                typer.echo(f"✗ 删除失败 {file_path}: {e}")

        for file_path in audio_files:
            try:
                file_path.unlink()
                typer.echo(f"✓ 删除音频: {file_path}")
                deleted_files += 1
            except Exception as e:
                typer.echo(f"✗ 删除失败 {file_path}: {e}")

        for file_path in text_files:
            try:
                file_path.unlink()
                typer.echo(f"✓ 删除文本: {file_path}")
                deleted_files += 1
            except Exception as e:
                typer.echo(f"✗ 删除失败 {file_path}: {e}")

        for file_path in markdown_files:
            try:
                file_path.unlink()
                typer.echo(f"✓ 删除Markdown: {file_path}")
                deleted_files += 1
            except Exception as e:
                typer.echo(f"✗ 删除失败 {file_path}: {e}")

        for video in videos:
            db.delete(video)

        db.commit()

        typer.echo()
        typer.echo("✓ 清理完成！")
        typer.echo(f"  删除文件: {deleted_files} 个")
        typer.echo(f"  删除记录: {len(videos)} 条")

    except Exception as e:
        logger.error("清理失败: %s", e, exc_info=True)
        db.rollback()
        raise typer.Exit(1)
    finally:
        db.close()


# ============================================================================
# test 命令 - 测试工具
# ============================================================================

@test_cli.command("feishu")
def test_feishu():
    """测试飞书推送是否正常"""
    from app.modules.push_channels.feishu import FeishuChannel, get_feishu_tenant_access_token

    typer.echo("测试飞书推送...")
    typer.echo("=" * 60)

    # 检查 token
    token = get_feishu_tenant_access_token()
    if not token:
        typer.echo("❌ 获取 token 失败", err=True)
        raise typer.Exit(1)
    typer.echo(f"✓ Token 获取成功: {token[:20]}...")

    # 发送测试消息
    channel = FeishuChannel()
    test_content = {
        "type": "dynamic",
        "title": "飞书推送测试",
        "text": "这是一条测试消息，发送时间: " + str(datetime.now()),
        "url": "https://example.com",
        "pub_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if channel.send(test_content):
        typer.echo("✓ 飞书推送成功!")
    else:
        typer.echo("❌ 飞书推送失败", err=True)
        raise typer.Exit(1)


@test_cli.command("wechat")
def test_wechat():
    """测试微信推送是否正常"""
    from app.modules.push_channels.wechat import WechatChannel

    typer.echo("测试微信推送...")
    typer.echo("=" * 60)

    channel = WechatChannel()
    test_content = {
        "type": "dynamic",
        "title": "微信推送测试",
        "text": "这是一条测试消息，发送时间: " + str(datetime.now()),
        "url": "https://example.com",
    }

    if channel.send(test_content):
        typer.echo("✓ 微信推送成功!")
    else:
        typer.echo("❌ 微信推送失败", err=True)
        raise typer.Exit(1)


@test_cli.command("all")
def test_all():
    """测试所有已配置的推送渠道"""
    from app.modules.push import get_enabled_channels
    from app.modules.push_channels import get_channel

    channels = get_enabled_channels()
    typer.echo(f"已配置的推送渠道: {channels}")
    typer.echo("=" * 60)

    results = {}
    for channel_name in channels:
        channel = get_channel(channel_name)
        if not channel:
            typer.echo(f"❌ {channel_name}: 渠道未找到")
            results[channel_name] = False
            continue

        # 构造测试内容
        test_content = {
            "type": "dynamic",
            "title": f"{channel_name} 推送测试",
            "text": f"这是一条测试消息，发送时间: {datetime.now()}",
            "url": "https://example.com",
        }

        if channel.send(test_content):
            typer.echo(f"✓ {channel_name}: 推送成功")
            results[channel_name] = True
        else:
            typer.echo(f"❌ {channel_name}: 推送失败")
            results[channel_name] = False

    typer.echo("=" * 60)
    success = sum(1 for v in results.values() if v)
    typer.echo(f"结果: {success}/{len(results)} 成功")

    if success < len(results):
        raise typer.Exit(1)


# ============================================================================
# 入口
# ============================================================================

if __name__ == "__main__":
    cli()