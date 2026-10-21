# -*- coding: utf-8 -*-
"""
mcdl_core.py —— MCdownloader 核心逻辑（纯标准库，无第三方依赖）

职责：
  1. 从镜像（默认 BMCLAPI 国内镜像，失败回退 Mojang 官方）获取 Minecraft 版本清单；
  2. 获取 Fabric / Forge / NeoForge 各加载器在指定 MC 版本下的可用版本与安装器地址；
  3. 下载游戏本体（版本 jar + 版本 json）到目标目录，调用 Java 运行加载器安装器，
     生成可被 PCL2 / HMCL / 官方启动器导入的实例目录。

说明：
  - 本模块不依赖网络即可被 import，网络相关函数均可在测试中打桩替换。
  - 加载器版本与安装器下载均优先走 BMCLAPI 镜像，失败时回退官方源。
"""

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "MCDownloader/1.0 (Doubao Workspace)"
SSL_CTX = ssl.create_default_context()

# ---------------------------------------------------------------------------
# 镜像 / 端点定义
# ---------------------------------------------------------------------------

BMCLAPI = "https://bmclapi2.bangbang93.com"
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_META_OFFICIAL = "https://meta.fabricmc.net"
FABRIC_MAVEN_OFFICIAL = "https://maven.fabricmc.net"
FORGE_PROMOS_OFFICIAL = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_MAVEN_OFFICIAL = "https://maven.neoforged.net/releases"

# 加载器类型常量
LOADER_NONE = "none"
LOADER_FABRIC = "fabric"
LOADER_FORGE = "forge"
LOADER_NEOFORGE = "neoforge"

LOADER_LABELS = {
    LOADER_NONE: "无（纯净版）",
    LOADER_FABRIC: "Fabric",
    LOADER_FORGE: "Forge",
    LOADER_NEOFORGE: "NeoForge",
}


# ---------------------------------------------------------------------------
# 网络基础
# ---------------------------------------------------------------------------

def _http_request(url, timeout=25):
    """发起 GET 请求，返回响应 bytes。失败抛异常。"""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        return resp.read()


def http_get_json(url, timeout=25):
    """GET 并解析 JSON。"""
    data = _http_request(url, timeout=timeout)
    return json.loads(data.decode("utf-8"))


def download_file(url, dest_path, timeout=60, log=None, expect_sha1=None):
    """流式下载 url 到 dest_path，支持进度回调 log(msg)。返回本地路径。

    - 会创建 dest_path 所在目录。
    - 若 expect_sha1 提供且文件已存在且 sha1 匹配，则跳过下载（断点续传友好）。
    - 下载完成后若提供 expect_sha1 会校验，不一致则删除并抛错。
    """
    dest_path = os.path.abspath(dest_path)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    if expect_sha1 and os.path.exists(dest_path):
        try:
            if _sha1_of(dest_path) == expect_sha1.lower():
                if log:
                    log(f"[跳过] {os.path.basename(dest_path)} 已存在且校验通过")
                return dest_path
        except OSError:
            pass

    tmp = dest_path + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        h = hashlib.sha1()
        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if log and total:
                    pct = done * 100 // total
                    if pct % 10 == 0 or done == total:
                        log(f"[下载] {os.path.basename(dest_path)} {done/1048576:.1f}/{total/1048576:.1f} MB ({pct}%)")
    if expect_sha1 and h.hexdigest().lower() != expect_sha1.lower():
        os.remove(tmp)
        raise RuntimeError(f"sha1 校验失败: {os.path.basename(dest_path)}")
    os.replace(tmp, dest_path)
    if log:
        log(f"[完成] 已保存 {dest_path}")
    return dest_path


def _sha1_of(path, chunk=1 << 16):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _try_each(urls, log=None, json_mode=False, **kw):
    """按顺序尝试多个 URL，返回第一个成功结果；全部失败抛最后异常。"""
    last = None
    for u in urls:
        try:
            if log:
                log(f"[尝试] {u}")
            if json_mode:
                return http_get_json(u, **kw)
            return _http_request(u, **kw)
        except Exception as e:  # noqa: BLE001
            last = e
            if log:
                log(f"[失败] {u} -> {e}")
    raise last


# ---------------------------------------------------------------------------
# Minecraft 版本清单
# ---------------------------------------------------------------------------

def fetch_versions(log=None):
    """返回 (latest_release, latest_snapshot, versions)。

    versions: list[dict]，字段 id / type / url / releaseTime / sha1，按 type 排序，
    正式版在前，快照次之，远古版本最后；每个 type 内按发布时间倒序。
    """
    manifest = _try_each(
        [BMCLAPI + "/mc/game/version_manifest.json", MOJANG_MANIFEST],
        log=log, json_mode=True,
    )
    latest_release = manifest.get("latest", {}).get("release", "")
    latest_snapshot = manifest.get("latest", {}).get("snapshot", "")

    order = {"release": 0, "snapshot": 1, "old_beta": 2, "old_alpha": 3}
    versions = manifest.get("versions", [])
    versions = [v for v in versions if v.get("type") in order]
    versions.sort(key=lambda v: (-order.get(v.get("type"), 9),
                                 v.get("releaseTime", ""), v.get("id", "")),
                  reverse=True)
    return latest_release, latest_snapshot, versions


def filter_versions(versions, want="all"):
    """按类型过滤：release / snapshot / all（正式+快照+旧版）。"""
    if want == "release":
        return [v for v in versions if v.get("type") == "release"]
    if want == "snapshot":
        return [v for v in versions if v.get("type") in ("release", "snapshot")]
    return versions


def download_version_json(mc_version, target_dir, log=None):
    """下载某版本的 version json 到 <target>/versions/<mc>/<mc>.json，并返回其 dict。"""
    # 先拿到清单中该版本的 url（BMCLAPI 会镜像下载地址）
    _, _, versions = fetch_versions(log=log)
    meta = next((v for v in versions if v.get("id") == mc_version), None)
    if not meta:
        raise RuntimeError(f"版本 {mc_version} 不在官方/镜像清单中")

    json_url = meta["url"]
    dest = os.path.join(target_dir, "versions", mc_version, mc_version + ".json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    # 使用 http_get_json 获取（同时用于返回解析结果）
    vdata = http_get_json(json_url)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(vdata, f, ensure_ascii=False, indent=2)
    if log:
        log(f"[完成] 版本 json -> {dest}")
    return vdata, dest


def download_client_jar(mc_version, target_dir, vdata=None, log=None):
    """下载 <mc>.jar 到 <target>/versions/<mc>/<mc>.jar。"""
    if vdata is None:
        vdata, _ = download_version_json(mc_version, target_dir, log=log)
    dl = (vdata.get("downloads") or {}).get("client") or {}
    url = dl.get("url")
    if not url:
        raise RuntimeError(f"版本 {mc_version} 缺少客户端下载地址")
    dest = os.path.join(target_dir, "versions", mc_version, mc_version + ".jar")
    return download_file(url, dest, log=log, expect_sha1=dl.get("sha1"))


# ---------------------------------------------------------------------------
# 加载器版本获取与安装器下载
# ---------------------------------------------------------------------------

def _bmlapi_maven_url(group_path, artifact, version, filename):
    """构造 BMCLAPI 镜像的 maven 文件 URL。group_path 如 'net/fabricmc/fabric-installer'。"""
    return f"{BMCLAPI}/maven/{group_path}/{artifact}/{version}/{filename}"


# ---- Fabric ----

def fetch_fabric_loaders(mc_version, log=None):
    """返回 (loader_versions, latest_loader_version)。"""
    meta = _try_each(
        [f"{BMCLAPI}/fabric-meta/v2/versions/loader/{mc_version}",
         f"{FABRIC_META_OFFICIAL}/v2/versions/loader/{mc_version}"],
        log=log, json_mode=True,
    )
    loaders = [e.get("loader", {}).get("version", "") for e in meta if e.get("loader")]
    loaders = [v for v in loaders if v]
    return loaders, (loaders[0] if loaders else "")


def fetch_fabric_installer_version(log=None):
    """获取最新 fabric-installer 版本号。"""
    xml = _try_each(
        [f"{BMCLAPI}/maven/net/fabricmc/fabric-installer/maven-metadata.xml",
         f"{FABRIC_MAVEN_OFFICIAL}/net/fabricmc/fabric-installer/maven-metadata.xml"],
        log=log,
    )
    root = ET.fromstring(xml)
    latest = root.findtext("latest")
    return (latest or "").strip()


def fabric_installer_url(installer_version):
    return _bmlapi_maven_url("net/fabricmc/fabric-installer", "fabric-installer",
                             installer_version, f"fabric-installer-{installer_version}.jar")


def build_fabric_cmd(java, installer_jar, mc_version, loader_version, target_dir):
    """构造 fabric-installer 的命令行参数（便于测试）。"""
    return [java, "-jar", installer_jar, "client",
            "-mcversion", mc_version, "-loader", loader_version,
            "-dir", target_dir, "-noprofile"]


# ---- Forge ----

def fetch_forge_builds(mc_version, log=None):
    """返回 forge 版本列表（按推荐/最新优先）。promotions 给出 recommended/latest。"""
    promos = {}
    try:
        p = _try_each(
            [f"{BMCLAPI}/forge/promotions_slim.json", FORGE_PROMOS_OFFICIAL],
            log=log, json_mode=True,
        )
        promos = p.get("promos", {}) or {}
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"[警告] promotions 获取失败（将仅按构建列表排序）: {e}")

    builds = _try_each(
        [f"{BMCLAPI}/forge/minecraft/{mc_version}"], log=log, json_mode=True,
    )
    # BMCLAPI 返回数组，每项含 version
    if isinstance(builds, list):
        build_versions = [b.get("version", "") for b in builds if b.get("version")]
    else:
        build_versions = list((builds or {}).get("versions", []))
    build_versions = [v for v in build_versions if v]

    recommended = promos.get(f"{mc_version}-recommended")
    latest = promos.get(f"{mc_version}-latest")

    def sort_key(v):
        return (0 if v == recommended else 1 if v == latest else 2)
    build_versions.sort(key=sort_key)
    return build_versions, recommended or (build_versions[0] if build_versions else "")


def fetch_forge_installer_url(mc_version, forge_version, log=None):
    """返回 forge 安装器 jar 的镜像下载地址。"""
    detail = http_get_json(f"{BMCLAPI}/forge/minecraft/{mc_version}/{forge_version}")
    installer = (detail or {}).get("installer") or {}
    url = installer.get("url")
    if not url:
        raise RuntimeError(f"Forge {forge_version} 未找到安装器地址")
    return url


def build_forge_cmd(java, installer_jar, target_dir):
    """构造 forge-installer 客户端安装命令（便于测试）。"""
    return [java, "-jar", installer_jar, "--installClient", target_dir]


# ---- NeoForge ----

def fetch_neoforge_builds(mc_version, log=None):
    """返回 neoforge 版本列表。优先 promotions_slim.json（BMCLAPI 镜像）。"""
    promos = {}
    try:
        p = _try_each(
            [f"{BMCLAPI}/neoforge/promotions_slim.json"], log=log, json_mode=True,
        )
        promos = p.get("promos", {}) or {}
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"[警告] NeoForge promotions 获取失败: {e}")

    versions = set()
    for k, v in promos.items():
        # 键形如 "1.21.1-recommended" / "1.21.1-latest"
        if k.startswith(mc_version + "-") and v:
            versions.add(v)
    recommended = promos.get(f"{mc_version}-recommended")

    # 若 promotions 没覆盖，尝试从 maven-metadata 补全（neo 版本形如 20.4.223-beta）
    if not versions:
        try:
            xml = _try_each(
                [f"{NEOFORGE_MAVEN_OFFICIAL}/net/neoforged/neoforge/maven-metadata.xml"],
                log=log,
            )
            root = ET.fromstring(xml)
            all_vers = [t.text for t in root.findall(".//version") if t.text]
            prefix = _neo_prefix_for_mc(mc_version)
            if prefix:
                versions.update(v for v in all_vers if v.startswith(prefix + "."))
        except Exception as e:  # noqa: BLE001
            if log:
                log(f"[警告] NeoForge maven-metadata 获取失败: {e}")

    ordered = sorted(versions, reverse=True)
    return ordered, recommended or (ordered[0] if ordered else "")


def _neo_prefix_for_mc(mc_version):
    """把 MC 版本号映射为 NeoForge 主版本前缀。

    1.20.4 -> 20.4 ; 1.20.5 -> 20.5 ; 1.21 -> 21.0 ; 1.21.1 -> 21.1 ...
    NeoForge 自 1.20.1 起，主版本号 = MC 的小版本 + 补丁位。
    """
    m = re.match(r"^1\.(\d+)(?:\.(\d+))?$", mc_version)
    if not m:
        return None
    minor, patch = m.group(1), m.group(2)
    if int(minor) >= 21 and patch:
        return f"{minor}.{patch}"
    return f"{minor}.{patch or 0}"


def neoforge_installer_url(neo_version):
    # 官方 maven，失败可回退 BMCLAPI maven 镜像
    return (f"{NEOFORGE_MAVEN_OFFICIAL}/net/neoforged/neoforge/{neo_version}/"
            f"neoforge-{neo_version}-installer.jar",
            _bmlapi_maven_url("net/neoforged/neoforge", "neoforge",
                              neo_version, f"neoforge-{neo_version}-installer.jar"))


def build_neoforge_cmd(java, installer_jar, target_dir):
    return [java, "-jar", installer_jar, "--installClient", target_dir]


# ---- 统一入口 ----

def fetch_loader_versions(loader_type, mc_version, log=None):
    """返回 (versions, default_version)。loader_type 为 LOADER_* 常量。"""
    if loader_type == LOADER_FABRIC:
        return fetch_fabric_loaders(mc_version, log=log)
    if loader_type == LOADER_FORGE:
        return fetch_forge_builds(mc_version, log=log)
    if loader_type == LOADER_NEOFORGE:
        return fetch_neoforge_builds(mc_version, log=log)
    return [], ""


def build_loader_cmd(loader_type, java, installer_jar, mc_version, loader_version, target_dir):
    if loader_type == LOADER_FABRIC:
        return build_fabric_cmd(java, installer_jar, mc_version, loader_version, target_dir)
    if loader_type == LOADER_FORGE:
        return build_forge_cmd(java, installer_jar, target_dir)
    if loader_type == LOADER_NEOFORGE:
        return build_neoforge_cmd(java, installer_jar, target_dir)
    return []


# ---------------------------------------------------------------------------
# Java 探测
# ---------------------------------------------------------------------------

def find_java(log=None):
    """返回 java 可执行文件路径；找不到返回 None。"""
    candidates = []

    # JAVA_HOME
    jh = os.environ.get("JAVA_HOME")
    if jh:
        for j in ("bin/java.exe", "bin/java"):
            p = os.path.join(jh, j)
            if os.path.isfile(p):
                candidates.append(p)

    # PATH 中的 java
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if not d:
            continue
        for j in ("java.exe", "java"):
            p = os.path.join(d, j)
            if os.path.isfile(p):
                candidates.append(p)

    # 常见安装位置
    for base in (r"C:\Program Files\Java", r"C:\Program Files (x86)\Java",
                 r"C:\Program Files\Microsoft", r"C:\Program Files\Eclipse Adoptium",
                 r"C:\Program Files\Zulu"):
        if os.path.isdir(base):
            for root, dirs, files in os.walk(base):
                for f in files:
                    if f.lower() in ("java.exe", "java"):
                        candidates.append(os.path.join(root, f))
                dirs[:] = []  # 不深入，顶层即可

    seen = set()
    for c in candidates:
        c = os.path.abspath(c)
        if c in seen:
            continue
        seen.add(c)
        try:
            r = subprocess.run([c, "-version"], capture_output=True, text=True, timeout=10)
            out = (r.stderr or "") + (r.stdout or "")
            m = re.search(r'version "([^"]+)"', out)
            ver = m.group(1) if m else "?"
            if log:
                log(f"[Java] 发现 {c} (版本 {ver})")
            return c
        except Exception:  # noqa: BLE001
            continue
    return None


# ---------------------------------------------------------------------------
# 安装编排
# ---------------------------------------------------------------------------

class InstallerError(RuntimeError):
    pass


def install(mc_version, loader_type, loader_version, target_dir, mirror="bmclapi",
            log=None, java=None, run=True):
    """执行完整安装。

    返回 dict: {mc_json, mc_jar, loader_installed(bool), loader_version, instance_dir}

    流程：
      1. 下载版本 json 与客户端 jar -> <target>/versions/<mc>/<mc>.{json,jar}
      2. 若选择加载器：下载安装器 jar 到临时目录，用 java 以 client 模式运行，
         生成 <target>/versions/<mc>-<loader>-<ver> 的可导入版本。
      3. 不做 loader 时，纯净版即为可导入版本。
    """
    if log is None:
        log = print
    target_dir = os.path.abspath(target_dir)
    os.makedirs(target_dir, exist_ok=True)
    log(f"=== 开始安装 Minecraft {mc_version} ===")
    log(f"[目标] {target_dir}")

    # 1. 版本本体
    log("[1/3] 下载游戏本体…")
    vdata, _ = download_version_json(mc_version, target_dir, log=log)
    download_client_jar(mc_version, target_dir, vdata=vdata, log=log)

    if loader_type == LOADER_NONE or not loader_version:
        log("[2/3] 未选择加载器，跳过")
        log("[3/3] 纯净版安装完成")
        return {
            "mc_json": os.path.join(target_dir, "versions", mc_version, mc_version + ".json"),
            "mc_jar": os.path.join(target_dir, "versions", mc_version, mc_version + ".jar"),
            "loader_installed": False,
            "loader_version": None,
            "instance_dir": os.path.join(target_dir, "versions", mc_version),
        }

    # 2. 加载器
    log(f"[2/3] 下载 {LOADER_LABELS[loader_type]} {loader_version} 安装器…")
    installer_jar = _fetch_installer(loader_type, mc_version, loader_version,
                                     target_dir, log=log)

    log("[3/3] 运行安装器（需要 Java）…")
    if not java:
        java = find_java(log=log)
    if not java:
        raise InstallerError("未找到 Java。请安装 Java 17+ 或设置 JAVA_HOME 后重试。")
    log(f"[Java] 使用 {java}")

    cmd = build_loader_cmd(loader_type, java, installer_jar, mc_version,
                           loader_version, target_dir)
    log("[命令] " + " ".join('"%s"' % c if " " in c else c for c in cmd))
    if not run:
        log("[测试] run=False，跳过实际执行")
        return {"cmd": cmd}

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise InstallerError("安装器运行超时（10 分钟）。可能是网络慢，可重试。")

    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines():
        if line.strip():
            log("  | " + line.strip())
    if proc.returncode != 0:
        raise InstallerError(f"安装器退出码 {proc.returncode}。详见上方输出。")

    # 找到生成的实例版本目录（形如 <mc>-<loader>-<ver> 或 <mc>-forge-<ver>）
    versions_dir = os.path.join(target_dir, "versions")
    instance_dir = _find_instance(versions_dir, mc_version)
    log("[完成] 加载器安装完成")
    log(f"[实例] {instance_dir}")
    return {
        "mc_json": os.path.join(target_dir, "versions", mc_version, mc_version + ".json"),
        "mc_jar": os.path.join(target_dir, "versions", mc_version, mc_version + ".jar"),
        "loader_installed": True,
        "loader_version": loader_version,
        "instance_dir": instance_dir,
    }


def _fetch_installer(loader_type, mc_version, loader_version, target_dir, log=None):
    """下载加载器安装器 jar 到 <target>/.mcdl_installers/ 并返回路径。"""
    dl_dir = os.path.join(os.path.abspath(target_dir), ".mcdl_installers")
    os.makedirs(dl_dir, exist_ok=True)

    if loader_type == LOADER_FABRIC:
        iv = fetch_fabric_installer_version(log=log)
        url = fabric_installer_url(iv)
        dest = os.path.join(dl_dir, f"fabric-installer-{iv}.jar")
        return download_file(url, dest, log=log)

    if loader_type == LOADER_FORGE:
        url = fetch_forge_installer_url(mc_version, loader_version, log=log)
        dest = os.path.join(dl_dir, f"forge-{loader_version}-installer.jar")
        return download_file(url, dest, log=log)

    if loader_type == LOADER_NEOFORGE:
        urls = neoforge_installer_url(loader_version)
        dest = os.path.join(dl_dir, f"neoforge-{loader_version}-installer.jar")
        data = _try_each(urls, log=log)
        with open(dest, "wb") as f:
            f.write(data)
        if log:
            log(f"[完成] 已保存 {dest}")
        return dest

    raise InstallerError(f"不支持的加载器: {loader_type}")


def _find_instance(versions_dir, mc_version):
    """在 versions 目录里找加载器生成的实例目录（排除纯 <mc> 本体）。"""
    if not os.path.isdir(versions_dir):
        return None
    try:
        entries = os.listdir(versions_dir)
    except OSError:
        return None
    # 形如 "1.21.1-forge-52.0.0" / "1.21.1-fabric-0.16.9" / "1.21.1-neoforge-21.1.0"
    for name in entries:
        if name.startswith(mc_version + "-"):
            return os.path.join(versions_dir, name)
    return os.path.join(versions_dir, mc_version)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def sanitize_version_id(v):
    """把可能来自用户输入的版本号规范化，防止路径穿越。"""
    v = re.sub(r"[^0-9A-Za-z._+-]", "-", v)
    v = re.sub(r"^\.+", "", v)  # 去掉开头点，避免形成 ".."
    return v.strip("-") or "unknown"
