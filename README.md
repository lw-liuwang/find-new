# find-new

搜索新闻、跟踪模型和项目更新，并保留可查询的本地历史。find-new 读取 Google News 搜索 RSS、RSS/Atom/RDF 订阅和公开 JSON API，按链接去重后保存到 SQLite，导出 JSON 与 Markdown。

需要 **Python 3.11+**，直接运行即可，无需安装第三方依赖。默认来源无需登录或 API Key，结果包含标题、链接、时间和来源提供的描述。

## 开始使用

```bash
git clone https://github.com/lw-liuwang/find-new.git
cd find-new
python3 -m find_new check
python3 -m find_new search 'TPU OR 推理加速'
```

`check` 离线检查来源配置。`search` 通过 Google News 搜索最近三天的新闻并保存；运行机器需要能够访问 Google News。

```bash
# 按配置采集全部启用来源。
python3 -m find_new collect

# 搜索最近七天的英文新闻。
python3 -m find_new search 'GPU kernel' --days 7 --language en-US --region US

# 查询本地历史，不访问网络。
python3 -m find_new list --query Qwen --limit 20 --format markdown
```

各命令的 `--help` 会显示参数用途和默认值。

## 选择来源

[sources.json](sources.json) 默认包含 AI 推理与算力新闻、vLLM Releases、ModelScope 新闻和 Qwen 模型更新。修改 `query` 调整新闻关键词，修改 ModelScope 查询的 `body.Path` 跟踪其他组织。

编辑后运行 `python3 -m find_new check`。添加订阅、映射 JSON 字段和配置 ModelScope 分页的方法见 [来源配置](docs/sources.md)。

## 读取结果

| 文件 | 内容 |
| --- | --- |
| `data/find-new.sqlite3` | 历史条目、首次与最近发现时间、HTTP 缓存 |
| `data/latest.json` | 本轮条目、来源状态、新增入库数量 |
| `data/latest.md` | 本轮条目的 Markdown 报告 |

`search` 和 `collect` 都会覆盖输出目录中的 `latest.*`，历史仍保留在 SQLite。`count` 表示本轮去重后的条数，`new_count` 只统计首次入库的链接。重复搜索可以返回同样的结果，同时显示新增为零。

同轮多个来源返回相同链接时，采用配置顺序中最先出现的条目，入库与导出使用同一内容。后续采集会更新标题、来源、描述和最近发现时间，保留首次发现时间。不同链接报道的同一事件不会合并。

需要分开保存时，可指定数据库和报告目录：

```bash
python3 -m find_new collect --config sources.json --db data/custom.sqlite3 --output-dir data/custom
```

采集与查询默认向标准输出打印 JSON，进度和错误写入标准错误。`--format markdown` 可切换输出格式。`data/` 已被 Git 忽略。

## 每日采集

[Collect](https://github.com/lw-liuwang/find-new/actions/workflows/collect.yml) 工作流计划每天北京时间 **08:17** 运行，也可在 Actions 页面手动触发。GitHub 调度可能延迟。

在运行页面查看 Summary 中的条目和失败来源，从 Artifacts 下载 `find-new-运行编号`，其中包含报告与数据库，保留 30 天。

工作流通过 Actions cache 恢复数据库。缓存被清理后会重新建立去重历史；需要长期保存历史时，请下载数据库备份。采集结果通过运行产物交付，不提交到仓库。

使用本机 cron 时，先手动采集一次以创建 `data` 目录，再按机器时区配置：

```cron
17 8 * * * cd /path/to/find-new && /usr/bin/python3 -m find_new collect >> data/collect.log 2>&1
```

## 失败处理与覆盖范围

单个来源失败会记录错误并继续采集。`collect` / `search` 返回 0 表示至少一个来源成功获取并解析，可能没有符合时间范围的条目。全部来源失败，或配置、存储、导出发生错误时返回 1；命令行参数用法错误返回 2。

同一主机的请求至少间隔两秒。网络或 HTTP 协议错误、429、5xx 最多重试一次，403 本次不重试。程序遵守有效的 `Retry-After`；要求等待超过 60 秒时跳过该来源。GET 支持 ETag 和 Last-Modified 条件请求，响应体超过 5 MiB 时停止下载。网络操作使用 20 秒超时，整轮耗时还取决于来源数量和重试。

日期缺失或无效的条目仍会保留，报告标记为“未提供”。搜索范围由 Google News 决定，链接可能是 Google 中转地址。ModelScope 默认查询组织列表第一页 20 条，其中包含置顶项，因此无法保证覆盖全部更新；时间表示仓库更新，不等于模型首次发布。

项目采集订阅和 API 提供的内容，不额外下载文章全文。开发与验证方法见 [CONTRIBUTING.md](CONTRIBUTING.md)。
