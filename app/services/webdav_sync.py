# -*- coding: utf-8 -*-
"""坚果云 WebDAV 手动同步服务。

纯手动触发：上传备份 = 把本地 JSON 打包成 zip 推到云端；
恢复数据 = 从云端拉取 zip 并覆盖本地 JSON。不包含任何自动同步。
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.models import Settings


class WebDavError(Exception):
    """WebDAV 操作失败时抛出的业务异常。"""


def _build_client(settings: Settings):
    """根据设置构建 webdav3 客户端；依赖缺失或配置不全时抛异常。"""
    try:
        from webdav3.client import Client
        from webdav3.exceptions import WebDavException
    except ImportError as exc:  # pragma: no cover
        raise WebDavError("未安装 webdavclient3 库，请先执行 pip install webdavclient3") from exc

    url = (settings.webdav_url or "").strip()
    user = (settings.webdav_user or "").strip()
    password = (settings.webdav_password or "").strip()
    if not url or not user or not password:
        raise WebDavError("请先在设置中填写完整的 WebDAV 地址、账号与应用密码")

    options = {
        "webdav_hostname": url.rstrip("/") + "/",
        "webdav_login": user,
        "webdav_password": password,
        "disable_check": False,
    }
    try:
        return Client(options), WebDavException
    except Exception as exc:  # noqa: BLE001
        raise WebDavError(f"WebDAV 客户端初始化失败：{exc}") from exc


def make_backup_bytes(base_dir: Path) -> bytes:
    """把数据目录里的 JSON 文件打包成内存中的 zip。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(base_dir.rglob("*.json")):
            if ".tmp" in path.name:
                continue
            arcname = path.relative_to(base_dir).as_posix()
            zf.write(path, arcname)
    return buffer.getvalue()


def apply_backup_bytes(base_dir: Path, data: bytes) -> list[str]:
    """用云端 zip 覆盖本地 JSON 文件，返回恢复的文件列表。"""
    restored: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json") and ".." not in n]
        for name in names:
            target = base_dir / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, target.open("wb") as dst:
                dst.write(src.read())
            restored.append(name)
    if not restored:
        raise WebDavError("云端备份包里没有找到任何 JSON 数据文件")
    return restored


def upload_backup(settings: Settings, base_dir: Path) -> None:
    """上传备份到云端。失败抛 WebDavError。"""
    client, _exc_cls = _build_client(settings)
    remote = (settings.webdav_remote or "moji_backup.zip").strip().lstrip("/")
    data = make_backup_bytes(base_dir)
    try:
        import io as _io

        # upload_to 要求父目录已存在：remote_path 带子目录时先逐级建目录
        parent = remote.rsplit("/", 1)[0] if "/" in remote else ""
        if parent and not client.check(parent + "/"):
            client.mkdir(parent, recursive=True)
        client.upload_to(_io.BytesIO(data), remote)
    except Exception as exc:  # noqa: BLE001
        raise WebDavError(f"上传失败：{exc}") from exc


def download_backup(settings: Settings, base_dir: Path) -> list[str]:
    """从云端下载并覆盖本地数据，返回恢复的文件列表。"""
    client, _exc_cls = _build_client(settings)
    remote = (settings.webdav_remote or "moji_backup.zip").strip().lstrip("/")
    try:
        if not client.check(remote):
            raise WebDavError("云端没有找到备份文件，请先上传一次备份")
        buffer = io.BytesIO()
        client.download_from(buff=buffer, remote_path=remote)
        return apply_backup_bytes(base_dir, buffer.getvalue())
    except WebDavError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise WebDavError(f"下载失败：{exc}") from exc
