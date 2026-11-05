# -*- coding: utf-8 -*-
"""坚果云 WebDAV 手动同步服务。

纯手动触发：上传备份 = 把本地 JSON 打包成 zip 推到云端；
恢复数据 = 从云端拉取 zip 并覆盖本地 JSON。不包含任何自动同步。

防护约定（体检项）：
- 网络错误按 401/403/404/429/507/超时翻译成可读中文；
- 429 / 5xx / 超时自动指数退避重试（默认 3 次，1s、2s）；
- 恢复先整包解压到内存并校验 CRC 与 JSON 语法，全部通过才落盘；
- 每个文件 tmp + os.replace 原子替换，覆盖前留 `.bak`，失败不留半截文件；
- 备份包路径非法（绝对路径 / 盘符 / ..）一律拒绝整包恢复。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

from app.models import Settings


class WebDavError(Exception):
    """WebDAV 操作失败时抛出的业务异常。"""


_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 1.0          # 秒，指数退避 1s / 2s
_MAX_BACKUP_BYTES = 200 * 1024 * 1024   # 单包上限 200MB，防异常大包拖垮内存

_COVER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def normalize_webdav_url(url: str) -> str:
    """规范化 WebDAV 地址：去重重复粘贴的协议头、补 https、确保以 / 结尾。"""
    value = (url or "").strip()
    if not value:
        return ""
    lower = value.lower()
    second = lower.find("http", 1)
    if second > 0:
        # 典型误操作：默认地址未清空又粘贴完整地址 → https://…/dav/https://…
        value = value[:second].rstrip()
        lower = value.lower()
    if not lower.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/") + "/"


DEFAULT_REMOTE_DIR = "催客写作"


def normalize_remote_dir(raw: str) -> str:
    """同步目录规范化：去首尾/重复斜杠、拒绝 . 与 ..，空值回退默认目录。

    只做规范化，不在存储层做百分号编码；实际请求由 _raw_request 用 quote 统一编码
    （中文与空格会编码成 %XX，不会用 + 替换空格）。
    """
    value = (raw or "").strip().replace("\\", "/")
    parts = [part.strip() for part in value.split("/") if part.strip()]
    if not parts:
        return DEFAULT_REMOTE_DIR
    if any(part in (".", "..") for part in parts):
        raise WebDavError("同步目录不能包含 . 或 ..，请填如「测试」或「写作备份/催客」")
    return "/".join(parts)


def remote_dir_of(settings: Settings) -> str:
    return normalize_remote_dir(getattr(settings, "webdav_dir", ""))


def build_remote_path(settings: Settings, filename: str) -> str:
    """统一的「同步目录 + 文件名」相对路径，供上传/恢复共用。"""
    return f"{remote_dir_of(settings)}/{filename.lstrip('/')}"


def backup_filename() -> str:
    return time.strftime("催客写作备份_%Y%m%d_%H%M%S.zip")


def _mkcol_once(settings: Settings, path: str) -> int:
    response = _raw_request(settings, "MKCOL", path + "/")
    code = response.status_code
    if code in (200, 201, 204, 405):   # 405 = 已存在
        return code
    raise WebDavError(_error_text(Exception(f"HTTP {code}")))


def ensure_remote_dir(settings: Settings, directory: str) -> list[str]:
    """逐级 MKCOL（最多 3 级）；405（已存在）视为成功，返回本次新建层级。

    不用 HEAD 探测存在性：坚果云对不存在的路径 HEAD 会返回 403，
    直接 MKCOL 更可靠，也能少一次请求。
    """
    parts = [part for part in directory.split("/") if part]
    if len(parts) > 3:
        raise WebDavError("同步目录最多支持 3 级，请简化路径")
    created: list[str] = []
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        try:
            code = _with_retry("创建云端目录",
                               lambda p=current: _mkcol_once(settings, p))
        except WebDavError as exc:
            if created:
                raise WebDavError(f"{exc}（已建成：{'、'.join(created)}）") from exc
            raise
        if code in (200, 201):
            created.append(current)
    return created


def _auth_pair(settings: Settings) -> tuple[str, str]:
    return ((settings.webdav_user or "").strip(), settings.webdav_password or "")


def _raw_request(settings: Settings, method: str, relative_path: str,
                 depth: int | None = None, data: bytes | None = None,
                 timeout: int = 25):
    """直连 requests 的轻量请求：首个请求即带 Basic Authorization（预认证）。

    requests 的 ``auth=(user, password)`` 使用 HTTPBasicAuth，会预先生成
    ``Authorization: Basic ...`` 头，不会等 401 挑战再重发，也不会被 session 去掉。

    ``data`` 传 bytes（不是 BytesIO）：requests 会自动带上 Content-Length。

    注意：不要用 webdav3 的 ``Client.upload_to``。它内部先调 ``check(parent)``，
    而 webdav3 的 check 走的是 **HEAD**——坚果云对 HEAD 一律返回 403，
    于是「测试连接绿、上传 403」，换账号/换目录都一样（已实测复现）。
    """
    import requests

    base = normalize_webdav_url(settings.webdav_url)
    url = base + quote(relative_path.lstrip("/"), safe="/")
    headers = {}
    if depth is not None:
        headers["Depth"] = str(depth)
    if data is not None:
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Length"] = str(len(data))   # 显式长度，绝不走 chunked
    return requests.request(method, url, auth=_auth_pair(settings),
                            headers=headers, data=data, timeout=timeout)


def _put_file(settings: Settings, relative_path: str, data: bytes) -> None:
    """PUT 上传（走原生 requests，绕开 webdav3 的 HEAD 探测）。"""

    def _do():
        response = _raw_request(settings, "PUT", relative_path, data=data, timeout=None)
        if response.status_code not in (200, 201, 204):
            raise Exception(f"HTTP {response.status_code}")
        return response

    _with_retry("上传", _do)


def _get_file(settings: Settings, relative_path: str) -> bytes:
    """GET 下载（原生 requests）。"""

    def _do():
        response = _raw_request(settings, "GET", relative_path, timeout=None)
        if response.status_code != 200:
            raise Exception(f"HTTP {response.status_code}")
        return response.content

    return _with_retry("下载", _do)


def _list_names(settings: Settings, directory: str) -> list[str]:
    """PROPFIND Depth 1 列出目录内文件名（原生 requests，不用 webdav3 的 list）。"""
    from urllib.parse import unquote
    import xml.etree.ElementTree as ET

    def _do():
        response = _raw_request(settings, "PROPFIND", directory + "/", depth=1)
        if response.status_code not in (200, 207):
            raise Exception(f"HTTP {response.status_code}")
        return response

    response = _with_retry("列出云端备份", _do)
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        return []
    prefix = "/" + directory.strip("/") + "/"
    names: list[str] = []
    for node in root.iter():
        if node.tag.split("}")[-1].lower() != "href" or not (node.text or "").strip():
            continue
        href = unquote(node.text.strip())
        index = href.find(prefix)
        if index < 0:
            continue
        rest = href[index + len(prefix):].strip("/")
        if rest and "/" not in rest:
            names.append(rest)
    return names


def _ensure_configured(settings: Settings) -> None:
    """校验 WebDAV 配置完整（主流程全部走原生 requests，不再依赖 webdav3）。"""
    url = normalize_webdav_url(settings.webdav_url)
    user = (settings.webdav_user or "").strip()
    password = settings.webdav_password or ""    # 原样发送：不 strip、不改大小写
    if not url or not user or not password.strip():
        raise WebDavError("请先在设置中填写完整的 WebDAV 地址、账号与应用密码")


def _error_text(exc: Exception, action: str = "") -> str:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "400" in text or name in ("badrequest",):
        return ("请求地址不合法（400）。「WebDAV 地址」只需填一个完整地址，例如 "
                "https://dav.jianguoyun.com/dav/ ，请勿重复粘贴。")
    if "409" in text or name in ("conflict",):
        return ("上级目录不存在或无权限（409）。请先创建上级目录，"
                "或把同步目录改成已存在的路径。")
    if "401" in text or name in ("unauthorized", "notauthenticated"):
        return ("用户名或应用密码不正确（401）。坚果云需使用「账户信息 → 安全选项 → "
                "第三方应用管理」生成的应用密码，不能用账号登录密码。")
    if "403" in text or name in ("forbidden", "notallowed"):
        if action == "上传":
            return ("云端拒绝写入（403）。目录可读但写入被拒，常见三类原因："
                    "①目标目录不存在或上级不存在（请用「测试连接」自动创建）；"
                    "②应用密码没有写权限（请确认该目录在网页版可编辑）；"
                    "③短时间请求过多被限流（请等 1–2 分钟再试）。")
        if action == "测试写入":
            return ("目录可读但不可写（403）。请确认该应用密码具备写入权限"
                    "（坚果云网页版里该目录应可新建文件）；若刚连续请求较多，"
                    "请等 1–2 分钟再试。")
        if action == "创建云端目录":
            return ("创建目录被拒（403）。请确认该应用密码有写权限，"
                    "且同步目录的上级目录存在；若刚连续请求较多，请等 1–2 分钟再试。")
        if action == "测试连接":
            return ("目录存在但访问被拒（403）。请确认应用密码有该目录的读权限；"
                    "若刚连续请求较多，请等 1–2 分钟再试。")
        return ("没有权限或请求被限制（403）。请确认该应用密码有权限访问此目录；"
                "若刚连续多次同步，请等 1–2 分钟再试。")
    if "507" in text or "notenoughspace" in name:
        return "云端空间不足（507）。请清理坚果云空间后重试。"
    if "423" in text or name in ("resourcelocked",):
        return ("云端资源被锁定（423）。请稍后重试；若持续失败，"
                "请在坚果云网页版解除同名文件/目录的锁定。")
    if "503" in text:
        return ("服务暂时不可用或被限流（503）。请等 1–2 分钟再试；"
                "若持续失败，请检查坚果云服务状态。")
    if any(code in text for code in ("500", "502", "504")) or name in (
            "servererror", "badgateway", "gatewaytimeout"):
        return "服务器暂时不可用（5xx）。请稍后重试；若持续失败，请检查坚果云服务状态。"
    if "429" in text or "toomanyrequests" in name or "频繁" in text:
        return "请求过于频繁（429）。请降低自动同步频率或稍等几分钟再试。"
    if "timed out" in text or "timeout" in text or "timeout" in name or "超时" in text:
        return ("连接超时。请检查网络或代理是否可用，稍后重试；"
                "若开启了 VPN / 系统代理，请确认坚果云域名未被拦截。")
    if "404" in text or name in ("noresource", "notfound"):
        return ("远端目录或备份文件不存在（404）。请在坚果云中新建该目录，"
                "或把备份路径改成已存在的目录。")
    if "ssl" in name or "certificate" in text:
        return ("SSL 证书校验失败。请检查系统时间是否正确、是否被代理软件篡改证书，"
                "必要时暂时关闭证书拦截后重试。")
    return str(exc) or type(exc).__name__


def _is_transient(exc: Exception) -> bool:
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if any(k in name for k in ("timeout", "connection", "toomanyrequests", "servererror")):
        return True
    return any(k in text for k in ("timed out", "connection", "429", "500", "502", "503", "504"))


def _with_retry(action: str, func):
    last: Exception | None = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return func()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if not _is_transient(exc) or attempt == _RETRY_ATTEMPTS - 1:
                break
            time.sleep(_RETRY_DELAY * (2 ** attempt))
    assert last is not None
    raise WebDavError(f"{action}失败：{_error_text(last, action)}") from last


def _cover_files(base_dir: Path) -> list[Path]:
    covers = base_dir / "covers"
    if not covers.is_dir():
        return []
    return [p for p in sorted(covers.iterdir())
            if p.is_file() and p.suffix.lower() in _COVER_SUFFIXES]


def make_cover_thumbnail(path: Path, max_side: int = 256) -> tuple[bytes, str] | None:
    """生成封面缩略图：长边 256px，WebP 优先（失败回退 JPEG），尽量落在 20–40KB。"""
    from PySide6.QtCore import QBuffer, QByteArray, Qt
    from PySide6.QtGui import QImage, QImageWriter

    image = QImage(str(path))
    if image.isNull():
        return None
    image = image.scaled(max_side, max_side,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    formats = {bytes(f).decode("ascii").lower()
               for f in QImageWriter.supportedImageFormats()}
    fmt, ext = ("WEBP", ".webp") if "webp" in formats else ("JPEG", ".jpg")
    if fmt == "JPEG":
        image = image.convertToFormat(QImage.Format.Format_RGB32)

    quality, best = 82, b""
    for _ in range(4):
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QBuffer.OpenModeFlag.WriteOnly)
        ok = image.save(buf, fmt, quality)
        buf.close()
        if not ok:
            return None
        best = bytes(data)
        size = len(best)
        if size > 40 * 1024 and quality > 50:
            quality -= 10
            continue
        if size < 20 * 1024 and quality < 95:
            quality += 5
            continue
        break
    return best, ext


def _safe_arcname(name: str) -> str | None:
    """把包内路径规范化；绝对路径 / 盘符 / .. / 空段一律视为非法。"""
    posix = name.replace("\\", "/")
    if not posix or posix.startswith("/") or re.match(r"^[A-Za-z]:", posix):
        return None
    parts = posix.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return posix


def _keep_kind(posix: str) -> str | None:
    name = posix.rsplit("/", 1)[-1]
    if ".tmp" in name:
        return None
    if posix.endswith(".json"):
        return "json"
    if (posix.startswith("covers/thumb/") and "/" not in posix[len("covers/thumb/"):]
            and Path(posix).suffix.lower() in _COVER_SUFFIXES):
        return "thumb"
    if (posix.startswith("covers/") and "/" not in posix[len("covers/"):]
            and Path(posix).suffix.lower() in _COVER_SUFFIXES):
        return "cover"
    return None


def make_backup_bytes(base_dir: Path, covers_mode: str = "thumb") -> bytes:
    """把 JSON 与封面打包成 zip。

    covers_mode：none 不带封面；thumb（默认）每本书只带 256px 缩略图；
    full 保留旧行为，原图整张进包。
    """
    mode = (covers_mode or "thumb").strip().lower()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(base_dir.rglob("*.json")):
            if ".tmp" in path.name:
                continue
            arcname = path.relative_to(base_dir).as_posix()
            zf.write(path, arcname)
        if mode == "full":
            for path in _cover_files(base_dir):
                zf.write(path, path.relative_to(base_dir).as_posix(),
                         compress_type=zipfile.ZIP_STORED)
        elif mode == "thumb":
            for path in _cover_files(base_dir):
                thumb = make_cover_thumbnail(path)
                if thumb is None:
                    continue
                data, ext = thumb
                # 命名用 bookid（原文件名主干），恢复端据此判断这本书有没有原图
                zf.writestr(f"covers/thumb/{path.stem}{ext}", data,
                            compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()


def apply_backup_bytes(base_dir: Path, data: bytes, report: dict | None = None) -> list[str]:
    """用云端 zip 覆盖本地 JSON 与封面，返回恢复的文件列表。

    先整包校验（CRC / 路径 / JSON 语法）再落盘；单个文件 tmp + os.replace 原子替换，
    覆盖前把本地旧文件备份成 `.bak`，中途失败不会留下半截目标文件。
    缩略图只在本地缺原图时顶上，绝不覆盖本地已有原图；
    report 里回填 thumbs_used / covers_kept 供界面提示「使用缩略图」。
    """
    if len(data) > _MAX_BACKUP_BYTES:
        raise WebDavError("云端备份包超过 200MB，已取消恢复（请确认备份文件是否异常）。")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise WebDavError("云端备份包已损坏（不是有效的 zip），请重新上传备份。") from exc

    thumbs_used = covers_kept = 0
    with zf:
        bad = zf.testzip()
        if bad is not None:
            raise WebDavError(f"云端备份包校验失败（{bad} 损坏），请重新上传备份。")
        staged: list[tuple[str, str, bytes, float]] = []
        for info in zf.infolist():
            if info.is_dir():
                continue
            posix = _safe_arcname(info.filename)
            if posix is None:
                raise WebDavError(f"备份包包含非法路径「{info.filename}」，已取消恢复。")
            kind = _keep_kind(posix)
            if kind is None:
                continue
            payload = zf.read(info.filename)
            if kind == "json":
                try:
                    json.loads(payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise WebDavError(
                        f"备份包里的「{posix}」不是有效的 JSON，已取消恢复。"
                    ) from exc
            stamp = time.mktime(info.date_time + (0, 0, -1))
            staged.append((posix, kind, payload, stamp))
        if not any(p.endswith(".json") for p, _k, _b, _s in staged):
            raise WebDavError("云端备份包里没有找到任何 JSON 数据文件")

        restored: list[str] = []
        for posix, kind, payload, remote_stamp in staged:
            if kind == "thumb":
                bookid = Path(posix).stem
                has_original = any(
                    p.suffix.lower() in _COVER_SUFFIXES for p in
                    (base_dir / "covers").glob(f"{bookid}.*")
                ) if (base_dir / "covers").is_dir() else False
                if has_original:
                    covers_kept += 1
                    continue
                # 本地没有原图：把缩略图放到原图位置顶上，界面可直接显示
                target = base_dir / "covers" / f"{bookid}{Path(posix).suffix}"
                thumbs_used += 1
            else:
                target = base_dir / posix
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                bak = target.with_name(target.name + ".bak")
                try:
                    shutil.copy2(target, bak)
                except OSError:
                    pass
                # 两端都改过：本地比云端更新时另存一份冲突副本，绝不静默丢弃
                try:
                    if target.stat().st_mtime > remote_stamp + 60:
                        stamp_text = time.strftime("%Y%m%d-%H%M%S")
                        conflict = target.with_name(f"{target.name}.{stamp_text}.conflict.bak")
                        shutil.copy2(target, conflict)
                except OSError:
                    pass
            tmp = target.with_name(target.name + ".restore.tmp")
            try:
                tmp.write_bytes(payload)
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            restored.append(target.relative_to(base_dir).as_posix())
    if report is not None:
        report["thumbs_used"] = thumbs_used
        report["covers_kept"] = covers_kept
    return restored


def upload_backup(settings: Settings, base_dir: Path, covers_mode: str | None = None) -> None:
    """上传备份到「同步目录」下（文件名带时间戳）。失败抛 WebDavError（含可读原因）。"""
    _ensure_configured(settings)
    # 上传前先做「读 + 建目录 + 写入探测」：目录不存在会被自动创建，
    # 写入被拒则在真正 PUT 大包之前就报出准确原因（不再用一个含糊的 403 兜底）。
    check = test_connection(settings)
    if not check.get("ok"):
        raise WebDavError(str(check.get("text") or "同步目录不可用"))
    mode = covers_mode or getattr(settings, "sync_covers_mode", "thumb")
    data = make_backup_bytes(base_dir, mode)
    if len(data) > _MAX_BACKUP_BYTES:
        raise WebDavError("本地备份包超过 200MB，已取消上传。请先清理封面或历史数据。")

    remote = build_remote_path(settings, backup_filename())
    _put_file(settings, remote, data)


def download_backup(settings: Settings, base_dir: Path, report: dict | None = None) -> list[str]:
    """从「同步目录」下载最新一份备份并覆盖本地数据，返回恢复的文件列表。"""
    _ensure_configured(settings)
    directory = remote_dir_of(settings)
    try:
        names = _list_names(settings, directory)
    except WebDavError as exc:
        raise WebDavError(
            f"{exc}（同步目录「{directory}」不存在或不可读）"
        ) from exc
    zips = [name for name in names if name.lower().endswith(".zip")]
    if not zips:
        raise WebDavError("云端没有找到备份文件，请先上传一次备份")
    latest = max(zips)
    remote = build_remote_path(settings, latest)
    data = _get_file(settings, remote)
    return apply_backup_bytes(base_dir, data, report)


def _delete_file(settings: Settings, relative_path: str) -> int:
    """DELETE 单个云端文件；404 视为「本来就不存在」也算成功。"""

    def _do():
        response = _raw_request(settings, "DELETE", relative_path)
        if response.status_code in (200, 204, 404):
            return response.status_code
        raise Exception(f"HTTP {response.status_code}")

    return _with_retry("删除云端备份", _do)


def delete_old_backups(settings: Settings, keep: int = 3) -> tuple[list[str], list[str]]:
    """删除旧备份，只保留最近 keep 份；返回 (已删除, 保留) 两个文件名列表。"""
    _ensure_configured(settings)
    directory = remote_dir_of(settings)
    names = sorted((name for name in _list_names(settings, directory)
                    if name.lower().endswith(".zip")), reverse=True)
    keep_count = max(0, int(keep))
    kept, removed = names[:keep_count], names[keep_count:]
    for name in removed:
        _delete_file(settings, build_remote_path(settings, name))
    return removed, kept


def create_remote_dir(settings: Settings) -> str:
    """按需创建同步目录（逐级 MKCOL ≤3 级），成功后复测一次。"""
    _ensure_configured(settings)
    directory = remote_dir_of(settings)
    created = ensure_remote_dir(settings, directory)
    result = test_connection(settings)
    if not result.get("ok"):
        raise WebDavError(str(result.get("text", "创建后复测失败")))
    if created:
        return f"已创建并可用：{'、'.join(created)}"
    return f"目录已存在且可用：{directory}"


def test_connection(settings: Settings) -> dict:
    """测试连接：读（PROPFIND）→ 建目录（MKCOL，仅在不存在时）→ 写入探测（PUT 后 DELETE）。

    三步全部通过才返回 ok=True，因此「测试连接通过」等价于「这个账号能往这个目录上传」。
    返回 {"ok", "missing", "created", "text", "steps"}；steps 为 [(阶段, HTTP 状态码)]。
    """
    directory = remote_dir_of(settings)
    steps: list[tuple[str, int | None]] = []
    created: list[str] = []
    probe_path = f"{directory}/.cuike_write_probe_{int(time.time() * 1000)}.txt"
    try:
        response = _with_retry(
            "测试连接",
            lambda: _raw_request(settings, "PROPFIND", directory + "/", depth=0),
        )
        code = response.status_code
        steps.append(("PROPFIND 同步目录", code))
        if code in (404, 409):
            # 目录不存在：按需创建（逐级 MKCOL，405 视为已存在）
            try:
                created = ensure_remote_dir(settings, directory)
            except WebDavError as exc:
                steps.append(("MKCOL 同步目录", None))
                return {"ok": False, "missing": True, "created": [],
                        "steps": steps,
                        "text": f"同步目录「{directory}」不存在且创建失败：{exc}"}
            steps.append(("MKCOL 同步目录", 201 if created else 405))
            response = _with_retry(
                "测试连接",
                lambda: _raw_request(settings, "PROPFIND", directory + "/", depth=0),
            )
            code = response.status_code
            steps.append(("PROPFIND 创建后", code))
        if code not in (200, 207):
            return {"ok": False, "missing": False, "created": created,
                    "steps": steps,
                    "text": _error_text(Exception(f"HTTP {code}"), "测试连接")}
        # 写入探测：写一个极小文件再删掉，验证「读通但写不通」不会漏判
        response = _with_retry(
            "测试写入",
            lambda: _raw_request(settings, "PUT", probe_path,
                                 data=b"cuike-write-probe"),
        )
        put_code = response.status_code
        steps.append(("PUT 探测文件", put_code))
        if put_code not in (200, 201, 204):
            return {"ok": False, "missing": False, "created": created,
                    "steps": steps,
                    "text": _error_text(Exception(f"HTTP {put_code}"), "测试写入")}
        try:
            delete_code = _raw_request(settings, "DELETE", probe_path).status_code
        except Exception:  # noqa: BLE001
            delete_code = None
        steps.append(("DELETE 探测文件", delete_code))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "missing": False, "created": created,
                "steps": steps, "text": _error_text(exc)}
    return {"ok": True, "missing": False, "created": created, "steps": steps,
            "text": f"连接正常 · 目录「{directory}」可读写"}
