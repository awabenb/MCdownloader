# -*- coding: utf-8 -*-
"""
mc_downloader.py —— Minecraft 下载器（tkinter GUI）

功能：
  1. 从镜像获取并选择 Minecraft 版本（正式版 / 快照 / 全部）；
  2. 选择加载器（无 / Fabric / Forge / NeoForge）及其版本；
  3. 下载游戏本体并运行加载器安装器，安装到指定目录，
     生成可被 PCL2 / HMCL / 官方启动器导入的实例。

运行：
  python mc_downloader.py

依赖：仅 Python 标准库（tkinter）。
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import mcdl_core as core

# 默认安装目录：%APPDATA%\.minecraft
DEFAULT_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), ".minecraft")

FILTER_LABELS = {"all": "全部版本", "release": "仅正式版", "snapshot": "正式版 + 快照"}
LOADER_IDS = [core.LOADER_NONE, core.LOADER_FABRIC, core.LOADER_FORGE, core.LOADER_NEOFORGE]


class App:
    def __init__(self, root):
        self.root = root
        root.title("MCdownloader —— Minecraft 版本/加载器下载安装器")
        root.geometry("760x620")
        root.minsize(640, 520)

        self._busy = False
        self._log_q = queue.Queue()
        self._version_meta = []          # 当前已加载的版本元数据（dict 列表）
        self._loader_versions = []       # 当前已加载的加载器版本
        self._java = core.find_java()

        self._build_widgets()
        self.root.after(100, self._drain_log)

        # 启动时自动加载 Java 信息
        if self._java:
            self._log(f"[Java] 已找到: {self._java}")
        else:
            self._log("[警告] 未找到 Java。安装加载器需要 Java 17+。")

    # ------------------------------------------------------------------ UI
    def _build_widgets(self):
        pad = {"padx": 8, "pady": 4}

        # 版本区
        f_ver = ttk.LabelFrame(self.root, text="1. 选择游戏版本")
        f_ver.pack(fill="x", **pad)

        self.var_filter = tk.StringVar(value="release")
        for key, label in FILTER_LABELS.items():
            ttk.Radiobutton(f_ver, text=label, value=key,
                            variable=self.var_filter, command=self._load_versions).pack(side="left", padx=6, pady=6)

        row = ttk.Frame(f_ver)
        row.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Label(row, text="版本:").pack(side="left")
        self.cmb_version = ttk.Combobox(row, state="readonly", width=26)
        self.cmb_version.pack(side="left", padx=6)
        ttk.Button(row, text="刷新版本列表", command=self._load_versions).pack(side="left", padx=4)

        # 加载器区
        f_loader = ttk.LabelFrame(self.root, text="2. 选择加载器")
        f_loader.pack(fill="x", **pad)

        lrow = ttk.Frame(f_loader)
        lrow.pack(fill="x", padx=6, pady=6)
        ttk.Label(lrow, text="加载器:").pack(side="left")
        self.cmb_loader = ttk.Combobox(
            lrow, state="readonly", width=18,
            values=[core.LOADER_LABELS[i] for i in LOADER_IDS])
        self.cmb_loader.current(0)
        self.cmb_loader.pack(side="left", padx=6)
        self.cmb_loader.bind("<<ComboboxSelected>>", lambda e: self._on_loader_change())

        ttk.Label(lrow, text="加载器版本:").pack(side="left", padx=(12, 0))
        self.cmb_lver = ttk.Combobox(lrow, state="readonly", width=24)
        self.cmb_lver.pack(side="left", padx=6)
        ttk.Button(lrow, text="刷新加载器", command=self._load_loaders).pack(side="left", padx=4)

        # 目录区
        f_dir = ttk.LabelFrame(self.root, text="3. 安装目录")
        f_dir.pack(fill="x", **pad)
        drow = ttk.Frame(f_dir)
        drow.pack(fill="x", padx=6, pady=6)
        self.var_dir = tk.StringVar(value=DEFAULT_DIR)
        self.ent_dir = ttk.Entry(drow, textvariable=self.var_dir)
        self.ent_dir.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(drow, text="浏览…", command=self._browse_dir).pack(side="left")

        # 操作区
        f_act = ttk.Frame(self.root)
        f_act.pack(fill="x", **pad)
        self.btn_install = ttk.Button(f_act, text="开始安装", command=self._start_install)
        self.btn_install.pack(side="left")
        ttk.Button(f_act, text="清空日志", command=self._clear_log).pack(side="left", padx=6)

        # 日志区
        f_log = ttk.LabelFrame(self.root, text="日志")
        f_log.pack(fill="both", expand=True, **pad)
        self.txt = tk.Text(f_log, height=16, wrap="word", font=("Consolas", 9),
                           state="disabled", bg="#f5f5f5")
        self.txt.pack(fill="both", expand=True, padx=4, pady=4)
        self.txt.tag_configure("err", foreground="#c0392b")
        self.txt.tag_configure("ok", foreground="#1e8449")

        self._load_versions()

    # ------------------------------------------------------------- helpers
    def _log(self, msg, tag=None):
        self._log_q.put((msg, tag))

    def _drain_log(self):
        try:
            while True:
                msg, tag = self._log_q.get_nowait()
                self.txt.configure(state="normal")
                self.txt.insert("end", msg + "\n", (tag or ""))
                self.txt.see("end")
                self.txt.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log)

    def _clear_log(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    def _browse_dir(self):
        sel = filedialog.askdirectory(title="选择安装目录", initialdir=self.var_dir.get() or DEFAULT_DIR)
        if sel:
            self.var_dir.set(sel)

    def _set_busy(self, busy):
        self._busy = busy
        state = "disabled" if busy else "normal"
        for w in (self.btn_install, self.cmb_version, self.cmb_loader, self.cmb_lver):
            try:
                w.configure(state=state)
            except tk.TclError:
                pass

    def _current_mc(self):
        return self.cmb_version.get().strip()

    def _current_loader_id(self):
        label = self.cmb_loader.get()
        for lid in LOADER_IDS:
            if core.LOADER_LABELS[lid] == label:
                return lid
        return core.LOADER_NONE

    # ------------------------------------------------------------ actions
    def _load_versions(self):
        if self._busy:
            return
        self._set_busy(True)
        self._log("正在获取版本清单（BMCLAPI 镜像，失败自动回退官方）…")
        th = threading.Thread(target=self._do_load_versions, daemon=True)
        th.start()

    def _do_load_versions(self):
        try:
            _, _, versions = core.fetch_versions(log=lambda m: self._log(m))
            self._version_meta = versions
            want = self.var_filter.get()
            filtered = core.filter_versions(versions, want)
            ids = [v["id"] for v in filtered]
            default = ""
            if ids:
                # 正式版优先选中最新正式版
                if want == "release":
                    default = ids[0]
                else:
                    rel = next((v["id"] for v in filtered if v.get("type") == "release"), ids[0])
                    default = rel
            self.root.after(0, self._populate_versions, ids, default)
            self._log(f"[完成] 共 {len(ids)} 个版本（当前筛选: {FILTER_LABELS[want]}）")
        except Exception as e:  # noqa: BLE001
            self._log(f"[错误] 获取版本清单失败: {e}", "err")
        finally:
            self.root.after(0, self._set_busy, False)

    def _populate_versions(self, ids, default):
        self.cmb_version.configure(values=ids)
        if default and default in ids:
            self.cmb_version.set(default)
        elif ids:
            self.cmb_version.current(0)
        else:
            self.cmb_version.set("")

    def _on_loader_change(self):
        lid = self._current_loader_id()
        if lid == core.LOADER_NONE:
            self.cmb_lver.configure(values=[])
            self.cmb_lver.set("")
        else:
            self._load_loaders()

    def _load_loaders(self):
        if self._busy:
            return
        lid = self._current_loader_id()
        mc = self._current_mc()
        if lid == core.LOADER_NONE:
            self.cmb_lver.configure(values=[])
            self.cmb_lver.set("")
            return
        if not mc:
            messagebox.showwarning("提示", "请先选择一个游戏版本")
            return
        self._set_busy(True)
        self._log(f"正在获取 {core.LOADER_LABELS[lid]} 在 {mc} 的可用版本…")
        th = threading.Thread(target=self._do_load_loaders, args=(lid, mc), daemon=True)
        th.start()

    def _do_load_loaders(self, lid, mc):
        try:
            versions, default = core.fetch_loader_versions(lid, mc,
                                                           log=lambda m: self._log(m))
            if not versions:
                self._log(f"[提示] {core.LOADER_LABELS[lid]} 可能不支持 {mc}，请换版本或加载器", "err")
            self._loader_versions = versions
            self.root.after(0, self._populate_loaders, versions, default)
            self._log(f"[完成] 找到 {len(versions)} 个 {core.LOADER_LABELS[lid]} 版本")
        except Exception as e:  # noqa: BLE001
            self._log(f"[错误] 获取加载器失败: {e}", "err")
        finally:
            self.root.after(0, self._set_busy, False)

    def _populate_loaders(self, versions, default):
        self.cmb_lver.configure(values=versions)
        if default and default in versions:
            self.cmb_lver.set(default)
        elif versions:
            self.cmb_lver.current(0)
        else:
            self.cmb_lver.set("")

    # ------------------------------------------------------------ install
    def _start_install(self):
        if self._busy:
            return
        mc = self._current_mc()
        lid = self._current_loader_id()
        lver = self.cmb_lver.get().strip()
        target = self.var_dir.get().strip()

        if not mc:
            messagebox.showwarning("提示", "请选择游戏版本")
            return
        if lid != core.LOADER_NONE and not lver:
            messagebox.showwarning("提示", "请选择加载器版本（或先点“刷新加载器”）")
            return
        if not target:
            messagebox.showwarning("提示", "请填写安装目录")
            return
        if not os.path.exists(target):
            try:
                os.makedirs(target)
            except OSError as e:
                messagebox.showerror("错误", f"无法创建目录:\n{target}\n{e}")
                return

        self._log(f"目标目录: {target}")
        self._set_busy(True)
        th = threading.Thread(target=self._do_install, args=(mc, lid, lver, target), daemon=True)
        th.start()

    def _do_install(self, mc, lid, lver, target):
        try:
            res = core.install(
                mc, lid, lver, target, mirror="bmclapi",
                log=lambda m: self._log(m),
                java=self._java,
            )
            self._log("=== 安装完成 ===", "ok")
            self._log(f"实例目录: {res.get('instance_dir')}", "ok")
            if res.get("loader_installed"):
                self._log("提示: 将上述实例目录用 PCL2/HMCL 导入，或在启动器中创建指向该目录的版本即可。", "ok")
            else:
                self._log("提示: 纯净版已就绪，可导入启动器。", "ok")
        except Exception as e:  # noqa: BLE001
            self._log(f"[错误] 安装失败: {e}", "err")
        finally:
            self.root.after(0, self._set_busy, False)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


def _log_base_dir():
    """日志写到 exe（或脚本）所在目录，保证用户能找到。"""
    if getattr(sys, "frozen", False):          # 打包成 exe 时
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _main_safe():
    """带错误兜底的入口：启动/运行异常时写入日志并弹窗，避免窗口一闪而过。"""
    try:
        main()
    except Exception:
        import traceback
        log_path = os.path.join(_log_base_dir(), "mc_downloader_error.log")
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("==== " + time.strftime("%Y-%m-%d %H:%M:%S") + " ====\n")
                traceback.print_exc(file=f)
                f.write("\n")
        except Exception:  # noqa: BLE001
            pass
        try:
            messagebox.showerror("启动失败", "程序启动出错，详情已写入:\n" + log_path)
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(1)


if __name__ == "__main__":
    _main_safe()
