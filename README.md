# find-new

按关键词搜索新闻，采集公开 RSS / Atom / JSON API，去重后保存到 SQLite，导出 JSON 和 Markdown。Python 3.11+，不需要安装第三方依赖，也不需要 API Key。

## 使用

在仓库根目录运行：

```bash
# 搜索最近三天的新闻；结果会同时保存到本地数据库。
python3 -m find_new search 'TPU OR 推理加速'

# 指定时间范围、语言和地区。
python3 -m find_new search 'GPU kernel' --days 7 --language en-US --region US

# 采集 sources.json 中的所有启用来源。
python3 -m find_new collect

# 查询已经保存的内容，不访问网络。
python3 -m find_new list --query TPU --limit 20 --format markdown
```

`search` 使用 Google News 搜索 RSS，并非通用网页搜索。网络环境需要能够访问所选来源。

默认输出：

| 文件 | 内容 |
| --- | --- |
| `data/find-new.sqlite3` | 历史条目、首次/最近发现时间、HTTP 缓存 |
| `data/latest.json` | 本轮结果、各来源状态、新增入库数量 |
| `data/latest.md` | 本轮结果的 Markdown 版本 |

这些运行数据不会提交到 Git。重复采集同一链接会更新已有记录，保留首次发现时间。`latest.*` 反映本轮匹配结果，可能包含以前已入库的条目；`new_count` 只统计首次入库的链接。历史内容通过 `list` 查询。

每个命令都支持 `--db 路径` 和 `--format json|markdown`；`collect` 和 `search` 还支持 `--output-dir 目录`。例如：

```bash
python3 -m find_new collect --config sources.json --db data/custom.sqlite3 --output-dir data/custom
```

## 配置信息源

编辑 `sources.json`。`window_hours` 是采集时间范围，默认 72 小时；`enabled: false` 可暂停一个来源。

默认来源是 AI 推理与算力新闻、vLLM 发布、ModelScope 新闻和 ModelScope 上 Qwen 的模型更新。

### ModelScope 模型更新

已接入无需登录的公开模型列表查询接口：

```json
{
  "name": "ModelScope · Qwen 模型更新",
  "kind": "json",
  "url": "https://modelscope.ai/api/v1/models",
  "method": "PUT",
  "body": {"Path": "Qwen", "PageNumber": 1, "PageSize": 20},
  "items_path": "Data.Models",
  "fields": {
    "title": "{Path}/{Name}",
    "url": "https://modelscope.ai/models/{Path}/{Name}",
    "description": "{Description} · 下载 {Downloads} · 收藏 {Stars}",
    "published": "LastUpdatedTime"
  }
}
```

把 `body.Path` 改为其他组织名即可跟踪对应列表。默认读取第一页 20 条，列表包含置顶项，因此不保证覆盖组织的所有新模型；需要更多内容时增大 `PageSize` 或添加具有不同 `PageNumber` 的来源。时间使用模型的 `LastUpdatedTime`，表示仓库更新，并非模型首次发布时间。默认使用已实测可达的国际站 `modelscope.ai`；可按网络情况把 API 和条目链接改为 `modelscope.cn`。

这一来源提供模型更新，不提供论文日榜。

### 关键词新闻

```json
{
  "name": "国内模型新闻",
  "kind": "google_news",
  "query": "DeepSeek OR Qwen OR 魔搭",
  "language": "zh-CN",
  "region": "CN"
}
```

程序会给查询加上时间范围。可使用 `OR`、引号等 Google News 搜索语法。返回链接可能是 Google News 中转地址，首版保留该地址。

### RSS / Atom / RDF

```json
{
  "name": "vLLM Releases",
  "kind": "rss",
  "url": "https://github.com/vllm-project/vllm/releases.atom"
}
```

三种 XML 格式共用 `rss` 类型。条目包含标题、链接、来源、发布时间，以及订阅源提供的描述。没有有效日期的条目仍会保留，但明确标记为“未提供”。

### JSON API

```json
{
  "name": "Example API",
  "kind": "json",
  "url": "https://example.com/api/articles",
  "items_path": "data.articles",
  "fields": {
    "title": "title",
    "url": "https://example.com/articles/{id}",
    "description": "summary",
    "published": "created_at"
  }
}
```

`items_path` 使用点分路径定位列表；根节点本身是列表时省略它。字段映射支持 `data.title` 这样的嵌套路径，以及 `{id}` 形式的模板。日期支持 RSS 日期、ISO 8601 和 Unix 秒时间戳。

JSON 来源默认使用 GET。对于公开的只读查询接口，可配置 `method: "POST"` 或 `"PUT"`，并用 `body` 提供 JSON 查询条件。不要用采集配置调用创建、修改或删除资源的接口。

## 定时采集

GitHub Actions 的 **Collect** 工作流每天计划在北京时间 **08:17** 执行，也可以在 Actions 页面手动运行。GitHub 调度可能延迟。

- 修改仓库中的 `sources.json` 即可调整定时采集来源。
- 在每次运行的 Artifacts 中下载 `find-new-运行编号`，里面包含 JSON、Markdown 和 SQLite 数据库。
- 数据库使用 Actions cache 在运行间恢复，下载的产物保留 30 天。缓存不是永久存储；缓存被清理后会重新建立去重历史，需要长期保存时请定期下载数据库。
- 工作流只需要读取仓库，不会自动提交数据或部署网页。

也可以在自己的机器上配置 cron：

```cron
17 8 * * * cd /path/to/find-new && /usr/bin/python3 -m find_new collect >> data/collect.log 2>&1
```

首次配置 cron 前先手动运行一次，创建 `data` 目录。机器时区决定 cron 的执行时间。

## 抓取行为

- 同一主机的请求至少间隔 2 秒，单次请求超时 20 秒。
- 利用服务端 ETag / Last-Modified 缓存；返回 304 时复用本地响应。
- 403 本次不重试；429 和 5xx 最多重试一次，遵守有效的 `Retry-After`。要求等待超过 60 秒时跳过该来源。
- 每个响应最多读取 5 MiB。单个来源失败会记录错误，继续处理其他来源；全部来源失败时命令退出码为 1。
- 去重基于链接，去除 UTM 等常见跟踪参数；不做跨媒体的语义去重。
- 只处理订阅/API 提供的标题和描述，不额外下载文章全文，不使用登录 Cookie，不调用模型生成摘要。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

测试使用本地 HTTP 服务和固定数据，不依赖外网。覆盖订阅格式解析、JSON 字段映射、持久化去重、条件请求、限流、来源失败隔离和命令行连续运行。

主要代码：`find_new/feeds.py` 解析与配置，`find_new/http.py` 网络请求，`find_new/store.py` 存储，`find_new/__main__.py` 命令行与导出。
