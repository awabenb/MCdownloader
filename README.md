# MCdownloader —— Minecraft 版本 / 加载器下载安装器

一个纯 Python 标准库实现的桌面工具（GUI），用于：

- 从镜像站获取并选择 **Minecraft 游戏版本**（正式版 / 快照 / 全部）；
- 选择 **加载器**（纯净版 / Fabric / Forge / NeoForge）及对应版本；
- 下载游戏本体并调用 Java 运行加载器安装器，**安装到指定目录**，
  生成可直接被 **PCL2 / HMCL / 官方启动器** 导入的实例。

默认走 **BMCLAPI 国内镜像**（快），网络异常时自动回退 **Mojang 官方源**。

---

## 环境要求

| 组件 | 是否必需 | 说明 |
|------|----------|------|
| Python 3.9+ | ✅ 必需 | 自带 tkinter 即可，无任何第三方依赖 |
| Java 17+ | ⚠️ 仅装加载器时 | 运行 Forge / Fabric / NeoForge 安装器需要。装纯净版可不要 |

本机已检测到 Java 17，可直接使用。

---

## 运行

方式一（推荐）：双击 `启动.bat`。

方式二：在项目目录打开命令行，执行

```
python mc_downloader.py
```

---

## 使用步骤

1. **选择游戏版本**：选「仅正式版 / 正式版+快照 / 全部版本」筛选，点「刷新版本列表」，
   从下拉框选一个版本（默认选中最新正式版）。
2. **选择加载器**：加载器下拉框选「无（纯净版）/ Fabric / Forge / NeoForge」，
   点「刷新加载器」获取该版本可用的加载器版本，再选中一个。
   - 部分加载器可能不支持你选的版本（如 NeoForge 只支持 1.20.1+，Fabric 不支持部分远古版），
     列表为空时请换版本或加载器。
3. **填安装目录**：默认 `%APPDATA%\.minecraft`（即官方启动器的数据目录），
   可点「浏览…」换成任意空目录。
4. 点 **「开始安装」**，观察日志。完成后日志会给出「实例目录」。

### 安装到指定目录后如何玩

- 若安装目录是 `%APPDATA%\.minecraft`，直接用**官方启动器**或 **HMCL / PCL2** 即可看到该版本。
- 若用的是自定义目录，在 **HMCL / PCL2** 的「版本列表 → 导入已有版本（.minecraft/versions）」
  中指向安装目录下的 `versions` 文件夹，或用启动器新建一个指向该目录的实例。
- 首次启动时启动器会自动补齐 libraries 与 assets 资源（无需本工具下载）。

---

## 文件结构

```
D:\MCdownloader\
├─ mc_downloader.py        # 图形界面入口（tkinter）
├─ mcdl_core.py            # 核心逻辑：镜像、版本/加载器解析、下载、安装
├─ 启动.bat                # 双击运行脚本
├─ README.md
└─ tests\
   ├─ test_offline.py      # 离线单元测试（不联网，mock 数据）
   └─ gui_smoke.py         # GUI 构建冒烟测试
```

安装到目标目录后生成的结构（以官方 .minecraft 为例）：

```
<安装目录>\
├─ versions\
│  ├─ 1.21.1\                 # 纯净版：版本 json + 客户端 jar
│  │  ├─ 1.21.1.json
│  │  └─ 1.21.1.jar
│  └─ 1.21.1-fabric-0.16.9\   # 加载器实例（安装器生成）
├─ libraries\                 # 加载器/游戏依赖库（启动器首次启动补齐）
└─ .mcdl_installers\          # 下载的加载器安装器缓存（可删）
```

---

## 工作原理与镜像

- **版本清单**：`https://bmclapi2.bangbang93.com/mc/game/version_manifest.json`
  （BMCLAPI 镜像），失败回退 `piston-meta.mojang.com` 官方。
- **Fabric**：`bmclapi2.../fabric-meta/v2/versions/loader/<mc>` 获取加载器版本；
  安装器从镜像 maven 下载 fabric-installer，运行
  `java -jar fabric-installer.jar client -mcversion <mc> -loader <ver> -dir <目录>`。
- **Forge**：`bmclapi2.../forge/promotions_slim.json` 推荐/最新 + `/forge/minecraft/<mc>` 构建列表；
  运行 `java -jar forge-installer.jar --installClient <目录>`。
- **NeoForge**：`bmclapi2.../neoforge/promotions_slim.json`；安装器从官方 maven
  （回退镜像 maven）下载，运行 `java -jar neoforge-installer.jar --installClient <目录>`。

下载有断点续传与 sha1 校验（版本 jar 校验）。

---

## 常见问题

| 问题 | 处理 |
|------|------|
| 点刷新没反应 / 日志报错 | 检查网络能否访问 `bmclapi2.bangbang93.com`；工具会自动回退官方源 |
| 提示「未找到 Java」 | 安装 Java 17+，或设置环境变量 `JAVA_HOME` 后重开 |
| 加载器列表为空 | 该版本不支持所选加载器，换一个版本或加载器 |
| 安装器报错退出码非 0 | 看日志里安装器的具体输出（多为下载依赖超时），重试即可 |
| 想卸载 | 删除对应 `versions/<版本>` 目录即可；`.mcdl_installers` 可随意删除 |

---

## 测试

核心逻辑已用 mock 数据做离线单测（不联网）：

```
python tests\test_offline.py
```

预期输出 `31 通过, 0 失败`。

> 注：工具依赖外网（镜像/官方），本说明给出的实时下载请在能联网的机器上运行。
