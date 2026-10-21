# -*- coding: utf-8 -*-
"""
test_offline.py —— 离线单元测试（不打真实网络，全部打桩）

运行：
  python tests/test_offline.py

覆盖：版本清单解析、筛选、加载器版本获取/排序、URL 构造、命令构造、
      sha1 断点续传、安装编排（mock 网络）、实例目录识别。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mcdl_core as core  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def fake_manifest():
    return {
        "latest": {"release": "1.21.1", "snapshot": "1.21.1-pre1"},
        "versions": [
            {"id": "1.21.1", "type": "release", "url": "https://x/1.21.1.json", "releaseTime": "2024-08-07T00:00:00Z"},
            {"id": "1.8.9", "type": "release", "url": "https://x/1.8.9.json", "releaseTime": "2015-12-09T00:00:00Z"},
            {"id": "1.21.1-pre1", "type": "snapshot", "url": "https://x/pre1.json", "releaseTime": "2024-07-30T00:00:00Z"},
            {"id": "b1.7.3", "type": "old_beta", "url": "https://x/b1.7.3.json", "releaseTime": "2011-07-08T00:00:00Z"},
            {"id": "c0.0.1", "type": "old_alpha", "url": "https://x/c0.json", "releaseTime": "2009-05-17T00:00:00Z"},
        ],
    }


def patch_http_get_json(fn):
    orig = core.http_get_json
    core.http_get_json = fn
    return orig


def test_version_manifest():
    print("== 版本清单解析 ==")
    orig = patch_http_get_json(lambda u, **k: fake_manifest())
    try:
        rel, snap, versions = core.fetch_versions()
        check("latest 解析", rel == "1.21.1" and snap == "1.21.1-pre1", f"{rel}/{snap}")
        check("排序: 正式版在前", versions[0]["id"] == "1.21.1", versions[0]["id"])
        # 同类型按时间倒序：1.21.1 在 1.8.9 前
        rel_ids = [v["id"] for v in core.filter_versions(versions, "release")]
        check("仅正式版", rel_ids == ["1.21.1", "1.8.9"], rel_ids)
        snap_ids = [v["id"] for v in core.filter_versions(versions, "snapshot")]
        check("正式+快照", "1.21.1-pre1" in snap_ids and "1.21.1" in snap_ids, snap_ids)
        all_ids = [v["id"] for v in core.filter_versions(versions, "all")]
        check("全部含旧版", "b1.7.3" in all_ids and "c0.0.1" in all_ids, all_ids)
    finally:
        core.http_get_json = orig


def test_forge_builds():
    print("== Forge 版本获取/排序 ==")
    fake_promos = {"promos": {"1.20.1-recommended": "1.20.1-47.2.0",
                              "1.20.1-latest": "1.20.1-47.3.0"}}
    fake_builds = [{"version": "1.20.1-47.1.0"}, {"version": "1.20.1-47.2.0"},
                   {"version": "1.20.1-47.3.0"}]

    calls = {"n": 0}
    def fake_get(url, **k):
        calls["n"] += 1
        if "promotions" in url:
            return fake_promos
        if "/minecraft/1.20.1" in url:
            return fake_builds
        raise RuntimeError("unexpected " + url)

    orig = patch_http_get_json(fake_get)
    try:
        versions, default = core.fetch_forge_builds("1.20.1")
        check("默认=recommended", default == "1.20.1-47.2.0", default)
        check("recommended 排最前", versions[0] == "1.20.1-47.2.0", versions)
        check("全部版本数", len(versions) == 3, versions)
    finally:
        core.http_get_json = orig


def test_neoforge_prefix():
    print("== NeoForge 主版本映射 ==")
    check("1.20.4 -> 20.4", core._neo_prefix_for_mc("1.20.4") == "20.4")
    check("1.20.5 -> 20.5", core._neo_prefix_for_mc("1.20.5") == "20.5")
    check("1.21 -> 21.0", core._neo_prefix_for_mc("1.21") == "21.0")
    check("1.21.1 -> 21.1", core._neo_prefix_for_mc("1.21.1") == "21.1")
    check("无效返回 None", core._neo_prefix_for_mc("foo") is None)


def test_commands():
    print("== 安装器命令构造 ==")
    c = core.build_fabric_cmd("java", "i.jar", "1.20.1", "0.15.11", r"D:\mc")
    check("fabric 命令", c == ["java", "-jar", "i.jar", "client", "-mcversion", "1.20.1",
                              "-loader", "0.15.11", "-dir", r"D:\mc", "-noprofile"], c)
    c = core.build_forge_cmd("java", "i.jar", r"D:\mc")
    check("forge 命令", c == ["java", "-jar", "i.jar", "--installClient", r"D:\mc"], c)
    c = core.build_neoforge_cmd("java", "i.jar", r"D:\mc")
    check("neoforge 命令", c == ["java", "-jar", "i.jar", "--installClient", r"D:\mc"], c)


def test_urls():
    print("== URL 构造 ==")
    u = core.fabric_installer_url("0.16.9")
    check("fabric installer url", "fabric-installer-0.16.9.jar" in u, u)
    u = core.neoforge_installer_url("21.1.52")
    check("neoforge installer url", "neoforge-21.1.52-installer.jar" in u[0], u)
    u = core._bmlapi_maven_url("net/fabricmc/fabric-installer", "fabric-installer", "0.16.9", "fabric-installer-0.16.9.jar")
    check("bmclapi maven url", "maven/net/fabricmc/fabric-installer" in u and "bmclapi2" in u, u)


def test_download_skip():
    print("== 下载断点续传/sha1 校验 ==")
    tmp = tempfile.mkdtemp(prefix="mcdl_test_")
    dest = os.path.join(tmp, "sub", "x.jar")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    data = b"hello world test data"
    with open(dest, "wb") as f:
        f.write(data)
    sha1 = core._sha1_of(dest)
    # 已存在且校验通过 -> 不联网直接跳过
    orig = core._http_request
    def boom(*a, **k):
        raise AssertionError("不应发起网络请求")
    core._http_request = boom
    try:
        out = core.download_file("http://never", dest, expect_sha1=sha1)
        check("sha1 匹配跳过下载", out == dest)
    finally:
        core._http_request = orig


def test_install_orchestration():
    print("== 安装编排（mock 网络 + run=False） ==")
    tmp = tempfile.mkdtemp(prefix="mcdl_inst_")
    target = os.path.join(tmp, "instance")
    mc = "1.21.1"

    fake_json = {"id": mc, "downloads": {"client": {"url": "http://x/mc.jar", "sha1": "abc123"}}}

    orig_get_json = core.http_get_json
    orig_dl = core.download_file
    orig_fetch_installer = core._fetch_installer
    orig_find_instance = core._find_instance

    def fake_get_json(url, **k):
        if "version_manifest" in url:
            return fake_manifest()
        if mc + ".json" in url:
            return fake_json
        raise RuntimeError("unexpected json url: " + url)

    def fake_dl(url, dest, **k):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"DUMMY")
        return dest

    def fake_fetch_installer(*a, **k):
        d = os.path.join(target, "i.jar")
        os.makedirs(os.path.dirname(d), exist_ok=True)
        with open(d, "wb") as f:
            f.write(b"INSTALLER")
        return d

    def fake_find_instance(vdir, vmc):
        inst = os.path.join(vdir, vmc + "-fabric-0.16.9")
        os.makedirs(inst, exist_ok=True)
        return inst

    core.http_get_json = fake_get_json
    core.download_file = fake_dl
    core._fetch_installer = fake_fetch_installer
    core._find_instance = fake_find_instance
    logs = []
    try:
        res = core.install(mc, core.LOADER_FABRIC, "0.16.9", target,
                           log=logs.append, java=r"C:\fake\java.exe", run=False)
        check("json 写入", os.path.exists(os.path.join(target, "versions", mc, mc + ".json")))
        check("jar 写入", os.path.exists(os.path.join(target, "versions", mc, mc + ".jar")))
        check("命令正确", res["cmd"][0] == r"C:\fake\java.exe", res["cmd"])
        check("有 fabric 参数", "-mcversion" in res["cmd"] and "0.16.9" in res["cmd"], res["cmd"])
        check("日志非空", len(logs) > 0, len(logs))
    finally:
        core.http_get_json = orig_get_json
        core.download_file = orig_dl
        core._fetch_installer = orig_fetch_installer
        core._find_instance = orig_find_instance


def test_install_none_loader():
    print("== 纯净版安装（无加载器） ==")
    tmp = tempfile.mkdtemp(prefix="mcdl_inst2_")
    target = os.path.join(tmp, "instance")
    mc = "1.21.1"
    fake_json = {"id": mc, "downloads": {"client": {"url": "http://x/mc.jar", "sha1": "abc123"}}}
    orig_get_json = core.http_get_json
    orig_dl = core.download_file

    def fake_get_json(url, **k):
        if "version_manifest" in url:
            return fake_manifest()
        return fake_json

    def fake_dl(url, dest, **k):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(b"DUMMY")
        return dest

    core.http_get_json = fake_get_json
    core.download_file = fake_dl
    try:
        res = core.install(mc, core.LOADER_NONE, "", target, log=lambda m: None, run=False)
        check("纯净版 loader_installed=False", res["loader_installed"] is False)
        check("纯净版实例目录", res["instance_dir"].endswith(os.path.join("versions", mc)),
              res["instance_dir"])
    finally:
        core.http_get_json = orig_get_json
        core.download_file = orig_dl


def test_find_instance():
    print("== 实例目录识别 ==")
    tmp = tempfile.mkdtemp(prefix="mcdl_fi_")
    vdir = os.path.join(tmp, "versions")
    os.makedirs(os.path.join(vdir, "1.20.1"), exist_ok=True)
    os.makedirs(os.path.join(vdir, "1.20.1-fabric-0.16.9"), exist_ok=True)
    inst = core._find_instance(vdir, "1.20.1")
    check("识别 loader 实例", inst.endswith("1.20.1-fabric-0.16.9"), inst)
    # 只有纯净版
    vdir2 = os.path.join(tmp, "v2")
    os.makedirs(os.path.join(vdir2, "1.20.1"), exist_ok=True)
    check("无 loader 时回退本体", core._find_instance(vdir2, "1.20.1").endswith(os.path.join("1.20.1")),
          core._find_instance(vdir2, "1.20.1"))


def test_sanitize():
    print("== 版本号净化 ==")
    check("路径穿越字符被替换", core.sanitize_version_id("../evil") == "evil")
    check("正常版本保留", core.sanitize_version_id("1.20.1") == "1.20.1")


if __name__ == "__main__":
    test_version_manifest()
    test_forge_builds()
    test_neoforge_prefix()
    test_commands()
    test_urls()
    test_download_skip()
    test_install_orchestration()
    test_install_none_loader()
    test_find_instance()
    test_sanitize()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
