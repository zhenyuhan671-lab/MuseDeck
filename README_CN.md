# MuseDeck

<p align="center">
  <strong>轻量（~14 MB）本地多账号指挥台 & Agent 浏览器调度池，专为 Meta Muse (muse.ai) 与 AI 创作工作流打造</strong>
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="#核心功能">核心功能</a> ·
  <a href="#快速开始">快速开始</a> ·
  <a href="#本地-agent-调度-api">本地 Agent API</a>
</p>

---

**MuseDeck** 是一款 100% 本地运行的 Windows 轻量化多账号管理与 Agent 浏览器指挥台，采用 **Python Core + Windows 原生 WebView2** 架构与磨砂毛玻璃现代交互界面。无需捆绑几百 MB 的魔改内核，直接调度本机原生 Chrome / Edge 实现物理级 Profile 隔离。

## 核心功能

- **Muse 周额度 + 赠送额度双进度条自动同步**：通过本地 CDP 自动读取 Muse 设置页的 **周额度百分比、重置日期** 与 **赠送 Token 余额**，支持不弹窗口的后台静默刷新（`--headless=new`）并持久化展示。
- **一键启动「空闲号」**：按真实已用百分比升序自动挑选最充裕的空闲账号启动。
- **Agent 专职角色（Soul）与常用派活指令库**：为每个账号设定角色定位与专属 Soul/常用指令，一键复制直接派活。
- **后台任务「待审批 / 新动态」与「掉登录」实时提醒**：实时监测运行窗口标题，免切窗口一眼掌握需要人工审批或掉线的账号。
- **防关联与住宅代理支持**：支持带 `username:password` 的 HTTP/SOCKS5 代理自动鉴权、WebRTC 防穿透、同 IP 冲突熔断拦截。
- **现代交互体验**：支持 **毛玻璃卡片 / 监控表格** 双视图切换、**右侧滑出式检视抽屉**、一键窗口网格平铺、多窗口同步广播网址、轻量 Cookie 会话导出/导入、默认插件模板继承、PIN 隐私隐身锁，以及可随时关闭 CDP 的 **100% 纯净原生模式**。

## 快速开始

### 方式一：绿色便携版（推荐）
前往 [Releases](../../releases) 下载 `MuseDeck.exe`，双击即用，所有数据仅保存在同级 `DeckData/` 目录中。

### 方式二：源码运行
```powershell
pip install -r requirements.txt
python main.py
```

### 运行测试
```powershell
python -m unittest discover -s tests -v
```

## 本地 Agent 调度 API

软件默认在 `127.0.0.1:18900` 提供仅限本机访问的轻量 HTTP API，方便 Playwright、n8n 或 AI Agent 直接接管已登录的隔离浏览器池：

- `GET http://127.0.0.1:18900/api/health`
- `GET http://127.0.0.1:18900/api/nodes`
- `POST http://127.0.0.1:18900/api/nodes/<id>/start`
- `POST http://127.0.0.1:18900/api/nodes/<id>/stop`

## 免责声明

本项目为独立开源本地工具，与 Meta / Muse 官方无任何关联。所有账号数据、Cookie 与配置均仅保存在你的本地电脑，绝不上传至任何服务器。

## 开源协议

[MIT](./LICENSE)
