# zsxq-pdf

知识星球研报备份工具：按 tag 分类同步帖子附件 PDF，并转换为 Markdown。

## 功能

- **双模式同步**：tag 模式（按标签精准拉取）和 full 模式（全量拉取 + 本地标签解析）
- **可编辑标签**：`tags.json` 配置文件，支持 CLI 增删管理
- **PDF 下载**：获取签名下载链接，自动下载 PDF 到本地，支持失败重试（指数退避）
- **PDF → Markdown**：基于 PyMuPDF 将 PDF 转为 Markdown 文本
- **分类存储**：文件按 `data/<tag>/<YYYYMMDD>/` 目录结构组织
- **SQLite 元数据**：所有帖子、附件、标签映射持久化到本地 SQLite

## 默认标签

首次 `init` 会生成 `data/tags.json`，包含脱敏后的示例标签：

| 标签名 | hid |
|--------|-----|
| 示例标签A | 10000000000001 |
| 示例标签B | 10000000000002 |
| 示例标签C | 10000000000003 |

可通过 `tag-add` / `tag-remove` 命令或直接编辑 `data/tags.json` 自定义。

## 目录结构

```
data/
├── tags.json                 # 标签配置（可编辑）
├── db/
│   └── app.sqlite3           # SQLite 数据库
├── 示例标签A/
│   └── 20260316/
│       ├── xxx.pdf
│       └── xxx.md
├── 示例标签B/
│   └── 20260316/
│       ├── xxx.pdf
│       └── xxx.md
├── 示例标签C/
│   └── ...
└── _unclassified/            # 未匹配任何标签的附件
    └── ...
```

## 安装

```bash
# 推荐使用 uv
uv venv
uv pip install -e .

# 或者 pip
python -m venv .venv
source .venv/Scripts/activate   # Windows
pip install -e .
```

依赖：Python >= 3.11, typer, rich, httpx, tenacity, pymupdf

## 使用

### 面向本地 AI agent 的终端模式

CLI 现在支持更适合自动化和 agent 调用的输出控制：

```bash
# 单条 JSON 结果
zsxq-pdf --json status --group <GROUP_ID>

# JSONL 事件流，适合长任务
zsxq-pdf --jsonl download --group <GROUP_ID> --cookies /path/to/cookies.txt

# 安静模式 + 禁用颜色
zsxq-pdf --quiet --no-color tag-list
```

新增辅助命令：

```bash
# 查看本地状态
zsxq-pdf --json status --group <GROUP_ID>

# 做本地环境自检
zsxq-pdf --json doctor --data-dir data --cookies /path/to/cookies.txt
```

长任务支持 `--dry-run` 预演：

```bash
zsxq-pdf --json download --group <GROUP_ID> --dry-run
zsxq-pdf --json convert --group <GROUP_ID> --dry-run
```

### 1) 初始化

```bash
zsxq-pdf init
```

创建 SQLite 数据库和默认 `tags.json`。

### 2) 认证检查

从浏览器导出 cookie（Netscape cookies.txt 格式），然后：

```bash
zsxq-pdf auth-check --group <GROUP_ID> --cookies /path/to/cookies.txt
```

### 3) 同步

两种模式可选：

```bash
# tag 模式（默认）：按 tags.json 中的标签逐个拉取
zsxq-pdf sync --group <GROUP_ID> --cookies /path/to/cookies.txt

# full 模式：通过 /v2/groups/{group}/files 全量拉取，本地解析标签
zsxq-pdf sync --group <GROUP_ID> --cookies /path/to/cookies.txt --mode full

# 只同步指定标签（tag 模式下）
zsxq-pdf sync --group <GROUP_ID> --cookies /path/to/cookies.txt -t 示例标签A -t 示例标签B

# 限制每个标签最多拉取页数
zsxq-pdf sync --group <GROUP_ID> --cookies /path/to/cookies.txt --max-pages 5
```

### 4) 下载 PDF

```bash
# 下载所有已同步的附件
zsxq-pdf download --group <GROUP_ID> --cookies /path/to/cookies.txt

# 只下载指定标签
zsxq-pdf download --group <GROUP_ID> --cookies /path/to/cookies.txt -t 示例标签A --no-include-unclassified

# 重试之前失败的
zsxq-pdf download --group <GROUP_ID> --cookies /path/to/cookies.txt --retry-failed
```

### 5) 转换为 Markdown

```bash
# 转换所有已下载的 PDF
zsxq-pdf convert --group <GROUP_ID>

# 只转换指定标签
zsxq-pdf convert --group <GROUP_ID> -t 示例标签A --no-include-unclassified
```

### 6) 标签管理

```bash
# 查看 tags.json 中的标签列表
zsxq-pdf tag-list

# 添加新标签
zsxq-pdf tag-add --name "新标签" --hid "12345678901234"

# 删除标签
zsxq-pdf tag-remove --name "新标签"
```

### 7) 辅助命令

```bash
# 查看各标签统计（从 DB 中读取）
zsxq-pdf tags --group <GROUP_ID>

# 对已有数据回填标签映射（适用于早期同步的数据）
zsxq-pdf backfill-tags --group <GROUP_ID>
```

## tags.json 格式

```json
[
  {
    "name": "示例标签A",
    "tag_id": "10000000000001",
    "url": "https://wx.zsxq.com/tags/%E7%A4%BA%E4%BE%8B%E6%A0%87%E7%AD%BEA/10000000000001"
  }
]
```

`url` 字段可选，省略时自动生成。

## 项目结构

```
src/zsxq_pdf/
├── cli.py                  # Typer CLI 入口（10 个子命令）
├── config.py               # AppConfig（data_dir / db_path）
├── zsxq/
│   ├── client.py           # ZSXQ API 客户端（hashtag topics / files / download_url）
│   └── cookies.py          # Cookie 加载（Netscape txt / JSON）
├── store/
│   ├── db.py               # SQLite schema（groups / topics / attachments / tags / topic_tags）
│   └── repo.py             # 数据访问层（upsert / query / tag stats）
├── download/
│   └── downloader.py       # HTTP 下载 + SHA256 校验
├── convert/
│   └── pdf_to_md.py        # PyMuPDF PDF → Markdown
└── util/
    ├── tags.py             # 标签注册表 + load/save tags.json + hashtag 解析
    ├── timefmt.py          # ZSXQ 时间格式 → YYYYMMDD
    └── sanitize.py         # 文件名清理
```

## API 端点

| 用途 | 端点 |
|------|------|
| 按标签拉取帖子（tag 模式） | `GET /v2/hashtags/{hid}/topics?count=30&end_time=...` |
| 全量拉取文件（full 模式） | `GET /v2/groups/{group_id}/files?count=20&index=...` |
| 获取下载链接 | `GET /v2/files/{file_id}/download_url` |

## 合规与安全

- 仅用于你本人账号的内容备份，请遵守平台使用条款
- Cookie 等同于账号凭证：不要提交到 git，不要发给他人
- 建议将 `cookies.txt` 和 `data/` 加入 `.gitignore`

## License

MIT
