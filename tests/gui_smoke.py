# -*- coding: utf-8 -*-
"""GUI 构建冒烟测试：屏蔽网络，仅验证界面组件可正常创建。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tkinter as tk
import mc_downloader as m

m.App._load_versions = lambda self: None          # 屏蔽启动时的网络加载
root = tk.Tk()
app = m.App(root)
root.update_idletasks()
print("cmb_loader values:", app.cmb_loader["values"])
print("loader options count:", len(app.cmb_loader["values"]))
print("java found:", bool(app._java))
root.destroy()
print("GUI build OK")
