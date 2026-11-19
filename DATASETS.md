# 数据集说明（Data Dictionary）

> 5 个数据集逐一说明：上游地址、许可证、规模、字段、**本项目取了哪些、没取哪些**、怎么拿全量。
> 所有抓取记录（URL / git sha / sha256 / 字节数）在 [`data/manifest.json`](data/manifest.json)。

---

## 1. chinese-poetry · 中华古典诗歌

| 项 | 内容 |
|---|---|
| 仓库 | <https://github.com/chinese-poetry/chinese-poetry> |
| 视频位置 | 00:08 |
| 许可证 | MIT |
| 上游规模 | ≈ 222MB（含 320 个唐诗分片、29 个宋词分片、图片与检索脚本） |
| 本项目取样 | 宋词**全量** 29 个 `ci.song.*.json` + `author.song.json`；唐诗 **12/320** 个 `poet.tang.*.json` + `authors.tang.json` |
| 入库 | `poetry_ci` 21,053 · `poetry_tang` 12,003 · `poetry_authors` 5,192 |

字段（宋词）：

```json
{"author": "和岘", "rhythmic": "导引", "paragraphs": ["气和玉烛，睿化著鸿明。", "..."]}
```

字段（唐诗）：`id`（UUID）、`author`、`title`、`paragraphs`（数组，跨行分句）。

入库时 `paragraphs` 用 `\n` 连接成 `paragraphs TEXT`，另算 `char_count` 便于统计。作者表把宋（`description`/`short_description`）与唐（`desc`）两个不同字段名统一成一张表，用 `dynasty` 区分。

**拿全量**：删掉 `scripts/fetch_data.py` 里 `limit` 那一行，重跑 `fetch_data.py --force --only poetry`（约 130MB 唐诗）。

典型问题：某诗人存诗多少？哪个词牌最常见？唐宋诗人小传与存诗量的关系（JOIN `poetry_authors`）。

---

## 2. word-atlas · 词光 · 文字图集

| 项 | 内容 |
|---|---|
| 仓库 | <https://github.com/designfuchen/word-atlas> |
| 视频位置 | 00:25 |
| 许可证 | 代码 MIT；数据 **CC BY 4.0**（`CONTENT_LICENSE.md`） |
| 上游规模 | `data/word-atlas.json` 8.1MB / 7,298 词 + 7,298 幅原创插画（插画不在本仓库） |
| 本项目取样 | `word-atlas.json` 全量 |
| 入库 | `words` 7,298 · `words_senses` 13,733 · `words_examples` 14,596 · `words_collocations` 24,722 |

字段：

```json
{"w": "library", "uk": "/ˈlaɪbrəri/", "us": "/ˈlaɪbreri/",
 "senses": [{"pos": "n.", "zh": "图书馆；藏书室", "en": "A place where people read or borrow books…"}],
 "ex": [{"en": "I borrowed three books from the library after school.", "zh": "放学后，我从图书馆借了三本书。"}],
 "col": ["public library｜公共图书馆"], "fam": ["librarian (n.)"], "syn": [], "ant": [],
 "tip": "library是借阅书籍的图书馆，bookshop是售书的书店。", "cefr": "A1", "topic": "education", "id": "library"}
```

为了能练 JOIN，`senses` / `ex` / `col` 拆成了三张子表（`word_id` + `idx` 复合主键），`fam/syn/ant` 暂存在原始 JSON 中未展开（单测里不依赖）。

**注意**：CC BY 4.0 要求署名。分享时请保留仓库链接与 `CONTENT_LICENSE.md`。

---

## 3. exercises-dataset · 健身动作库

| 项 | 内容 |
|---|---|
| 仓库 | <https://github.com/hasaneyldrm/exercises-dataset> |
| 视频位置 | 00:48 |
| 许可证 | 仓库标注 `NOASSERTION`，素材版权见 `NOTICE.md`（动作示范 © Gym visual） |
| 上游规模 | ≈ 125MB（主要是 1,324 个 GIF 与图片）；`exercises.json` 17.4MB |
| 本项目取样 | `exercises.json` 全量，**不含** GIF/图片素材 |
| 入库 | `ex_exercises` 1,324 |

字段：`id`、`name`、`category`（waist/chest/…）、`body_part`、`equipment`、`muscle_group`、`target`、`secondary_muscles`、`instructions`（多语言，取 `en`）、`gif_url`、`media_id`、`attribution`。

`instructions` 是 `{en, it, …}` 对象，入库只保留英文正文；多语言原文仍在 `data/raw/exercises/data/exercises.json`。

许可证提示：原仓 `NOASSERTION` 意味着**不能想当然当 MIT 用**，搬运请读 `NOTICE.md` 并保留 attribution（已随数据保存并在页面展示）。

---

## 4. countries-states-cities-database

| 项 | 内容 |
|---|---|
| 仓库 | <https://github.com/dr5hn/countries-states-cities-database> |
| 视频位置 | 01:08 |
| 许可证 | **ODbL-1.0**（开放数据库许可，衍生库需同样开放） |
| 上游规模 | 仓库历史 ≈ 7.6GB；`json/countries+states+cities.json` 单文件 59MB |
| 本项目取样 | `json/countries.json` + `json/states.json` + `json/countries+cities.json`（4.5MB，含 171,267 城） |
| 入库 | `geo_countries` 250 · `geo_states` 5,327 · `geo_cities` 171,267 |

字段要点：

- 国家：`id/name/iso2/iso3/capital/region/subregion/population/currency/phonecode/area_sq_km/latitude/longitude`
- 州省：`id/name/country_id/country_code/country_name/iso2/type/latitude/longitude/timezone`（`type` 区分 province/state/…，还有 `level`、`parent_id` 支持多级行政区）
- 城市：上游 `countries+cities.json` 只有 `{name, cities:[…]}`，没有城市 ID；本项目按国家名回填 `country_id`（对不上的留 `NULL`）

城市没有经纬度是上游数据本身的结构（要经纬度得用 59MB 的合并文件或 `geojson/` 目录）。

ODbL 提示：如果把这个库做成对外服务，需要按 ODbL 共享衍生数据库。

---

## 5. The Well · 15TB 物理模拟数据集

| 项 | 内容 |
|---|---|
| 仓库 | <https://github.com/PolymathicAI/the_well> |
| 视频位置 | 01:32 |
| 许可证 | BSD-3-Clause（代码）；数据本身另有 Hugging Face 数据集条款 |
| 上游规模 | **15TB**，16 类物理场、26 个数据集目录 |
| 本项目取样 | 只取 `datasets/*/*.yaml` 元数据（22 个可解析数据集）+ `README.md`，**不下载任何数值数据** |
| 入库 | `well_datasets` 22 |

入库字段：`dataset`、`dims`（空间维度）、`resolution`、`n_files`、`n_steps`、`n_traj`、`grid_type`、`boundary`、`fields`（场变量清单）、`scalars`。

YAML 用脚本里 60 行的极简解析器读取（不引入 PyYAML，保持"标准库即可跑"）。

**拿数据**（需另装依赖，本项目未内置）：

```python
from the_well.data import WellDataset
from torch.utils.data import DataLoader

train = WellDataset(name="MHD_64", split="train")
loader = DataLoader(train, batch_size=2)
```

下载走 Hugging Face（`polymathic-ai/` 组织），单个数据集从几 GB 到数百 GB 不等。

---

## 取样策略总结

| 数据集 | 上游体积 | 本项目落地 | 比例 |
|---|---|---|---|
| chinese-poetry | ≈222MB | 15MB | 唐诗 12/320 片，宋词全量 |
| word-atlas | ≈8MB + 插画 | 7.8MB | 词条 100% |
| exercises-dataset | ≈125MB | 17MB | 动作 100%（不含媒体） |
| countries-states-cities | 7.6GB 仓库 | 12MB | 国家 100%、州省 100%、城市 100%（无经纬度） |
| The Well | 15TB | 204KB | 仅元数据 |
| **合计** | — | **≈51MB** | |

取舍原则：**能在本机秒查、能塞进一个公开仓库、许可证都看得见**，比"看起来很大"更重要。
