# 跨平台使用说明 (Windows / macOS / Linux)

同一套代码在三个平台上运行，平台差异全部放在配置和系统钥匙串里，脚本中不写任何个人路径。
CI 在 Ubuntu、Windows、macOS 上各跑一遍测试。

---

## 1. 先自检

换机器、换 Python 环境后先运行：

```bash
python tools/doctor.py          # 人看
python tools/doctor.py --json   # 给 Agent 读
```

它只读不改，报告本机**实际**具备的能力：PDF 后端、OpenDataLoader 与 Java 版本、MinerU CLI、qpdf、keyring、
MinerU token（只报告是否存在和长度）、配置文件与白名单目录是否存在，并给出建议。
不要照搬另一台机器的结论——同一个 skill，Mac 上有 CLI 和云 API，Windows 办公机上可能只有图形版。

## 2. 配置文件位置

| 平台 | 默认路径 |
|---|---|
| Windows | `%APPDATA%\engineering-pdf-pipeline\config.json` |
| macOS | `~/Library/Application Support/engineering-pdf-pipeline/config.json` |
| Linux | `$XDG_CONFIG_HOME/engineering-pdf-pipeline/config.json`（默认 `~/.config/...`） |

也可以用环境变量 `PDF_PIPELINE_CONFIG` 指定任意位置，或在命令行加 `--config`。
格式见仓库根目录的 [`config.example.json`](../config.example.json)。路径支持 `~`；Windows 路径写成 `D:/Library/Standards`
或 `D:\\Library\\Standards`（JSON 中反斜杠要转义）。配置文件用 UTF-8 保存，带 BOM 也能读。

| 键 | 含义 |
|---|---|
| `cloud_allowed_dirs` | 允许上传云 API 的目录（公开规范、出版物）。**未配置时一律拒绝上传** |
| `output_dir` | `mineru_cloud.py` 的默认输出目录 |
| `mineru.*` | 云 API 地址、模型、语言、单文件页数上限、轮询间隔、超时、钥匙串条目名 |

## 3. MinerU token 存放

优先级：环境变量 `MINERU_API_TOKEN` > 系统钥匙串（需要 `pip install keyring`）。

`keyring` 在三个平台分别对接 macOS Keychain、Windows 凭据管理器、Linux Secret Service（GNOME Keyring / KWallet）。
写入方法三平台相同，输入时屏幕不回显：

```bash
python -c "import keyring,getpass; keyring.set_password('mineru-api','token',getpass.getpass('MinerU token: '))"
```

条目名可在配置 `mineru.keyring_service` / `mineru.keyring_user` 中修改。不要把 token 写进配置文件、脚本或聊天记录；
曾经明文出现过的 token 到控制台作废重建。

## 4. 编码

- 所有工具读写文件一律显式 `encoding="utf-8"`。
- 标准输出被重定向到文件或管道时统一改为 UTF-8（中文 Windows 默认会按 GBK 写出）；交互式控制台不受影响。
- 调用外部命令：判断是否存在用 `shutil.which()`；需要读输出时用 `tools/_compat.py` 的 `run_text()`，
  它先按 UTF-8、再按系统代码页解码。原因见 [坑 19](pitfalls.md)。

## 5. 各平台注意事项

| 项目 | Windows | macOS | Linux |
|---|---|---|---|
| OpenDataLoader | 需要 Java 11+；老机器常见的 Oracle Java 8（`Program Files (x86)`）不满足 | `brew install openjdk` | 发行版 OpenJDK |
| 拆分 PDF | `qpdf`（winget / scoop）或 PyMuPDF | `brew install qpdf` | 包管理器安装 `qpdf` |
| MinerU 本地 CLI | 依赖的深度学习包未必支持最新 Python，建议单独建 3.11/3.12 虚拟环境 | 同左 | 同左 |
| 模型下载 | 国内网络可设 `MINERU_MODEL_SOURCE=modelscope` | 同左 | 同左 |
| 路径 | 白名单比较不区分大小写，跨盘符不会误判 | 区分大小写取决于文件系统 | 区分大小写 |
