# 来源配置

`collect` 默认读取工作目录中的 `sources.json`，也可通过 `--config` 指定文件。下面是一份可以直接使用的最小配置：

```json
{
  "window_hours": 72,
  "sources": [
    {
      "name": "国内模型新闻",
      "kind": "google_news",
      "query": "DeepSeek OR Qwen OR 魔搭",
      "language": "zh-CN",
      "region": "CN"
    }
  ]
}
```

将新增来源放入 `sources` 数组，每个来源使用非空且唯一的 `name`。设置 `"enabled": false` 可暂停该来源。

`window_hours` 默认 72，允许大于 0 且不超过 8760 小时。有有效发布时间的条目按这个窗口过滤；没有有效日期的条目仍保留。

编辑后执行：

```bash
python3 -m find_new check --config sources.json
```

命令离线校验配置，输出来源总数、启用数和时间窗口，不创建数据库。远端地址是否可用，需要通过实际采集确认。

## 新闻搜索

`kind: "google_news"` 使用 `query` 指定关键词，支持引号、`OR` 等 Google News 搜索语法。`language` 默认 `zh-CN`，`region` 默认 `CN`。

程序追加按天取整的搜索范围，再按条目日期执行小时级过滤。无需在 `query` 中重复填写 `when:`。单次搜索可直接运行：

```bash
python3 -m find_new search 'GPU kernel OR TPU' --days 7 --language en-US --region US
```

搜索覆盖范围由 Google News 决定，结果可能保留 Google 中转链接。

## RSS、Atom 与 RDF

三种格式共用 `kind: "rss"`：

```json
{
  "name": "vLLM Releases",
  "kind": "rss",
  "url": "https://github.com/vllm-project/vllm/releases.atom"
}
```

解析器读取标题、文章链接、描述和发布时间。Atom 优先使用 `published` 日期和文章的 `alternate` 链接，相对链接按订阅地址解析。

## JSON API

通过 `items_path` 定位列表，用 `fields` 映射条目字段：

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

根节点本身是列表时，省略 `items_path`。字段路径采用 `paper.title` 这样的点分格式；模板中的 `{id}` 或 `{paper.id}` 会替换为对应值。

`title` 和 `url` 必须配置，缺少标题或链接的条目会丢弃。`description` 和 `published` 可以省略。日期支持 RSS 日期、ISO 8601 和 Unix 秒时间戳。

默认使用 GET。公开的只读查询接口也可使用 `method: "POST"` 或 `"PUT"`，通过 `body` 提供 JSON 对象。采集配置不应用于创建或修改资源的 API。

## ModelScope 模型更新

默认来源通过公开接口查询 Qwen 组织的模型列表：

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

修改 `body.Path` 可跟踪其他组织。需要更多结果时，增加 `PageSize`，或为不同 `PageNumber` 添加来源并使用不同的 `name`。程序按配置查询，不自动翻页。

列表包含置顶模型，第一页不保证覆盖全部更新。`LastUpdatedTime` 表示模型仓库最近更新，结果不能直接作为新模型发布清单。该来源提供模型更新，不提供论文日榜。

默认使用国际站 `modelscope.ai`。若使用 `modelscope.cn`，请同时修改 API 地址与 `fields.url` 中的域名。
