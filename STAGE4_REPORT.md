# 第四阶段交付与自测记录

执行日期：2026-11-12，北京时间。基线 HEAD 为 `088b2a4`，前三阶段提交 `5acca1e`、`a899fe0`、`088b2a4` 已核实。已读取并遵守根目录 AGENTS.md。本阶段只做免费现货源、部署配置/说明与自测，没有连接或部署云服务器。

## 变更摘要

新增 10 个逐品种 adapter 文件，全部真实运行 `fetch() → SQLite upsert → latest 回读` 成功后才继续下一个，进度按完成顺序追加到 [STAGE4_PROGRESS.md](STAGE4_PROGRESS.md)。每个品种固定来源、规格与计价口径；本次没有跳过品种，没有填造行情或期货替代值。

公共解析代码位于 `plasway.py`、`yjgmall.py`，逐品种入口分别保留。来源注册、品种配置仍维护在 `backend/app/config.py`。每个品种独立来源状态，某源失败不阻止其余源；同站点不同 adapter 共享请求间隔。新增安全检查识别，拒绝执行上游校验脚本。

`adapters/sci99.py` 与基线完全一致。调度层把卓创明确设为休眠，状态仍显示“待配置凭据”；即便误填凭据也不会请求。全部 11 品种均已与卓创解耦，当前生产库 `source_runs` 记录数为 0。没有创建或修改任何卓创凭据。

页面更新空数据/异常说明，展示完整固定口径，不再把已接入品种称为“待接入”。旧来源、旧口径价格不混入当前历史或涨跌；旧口径的提醒规则仍按第三阶段逻辑跳过，需在页面编辑保存重新确认，不自动套用旧阈值。

部署只产出 [docker-compose.yml](docker-compose.yml) 与 [DEPLOYMENT.md](DEPLOYMENT.md)：内联多阶段构建、Python 3.12、Vue 静态托管、一个 worker、SQLite 持久卷、只读根目录 `.env` 挂载、健康检查、进程重启策略与日志轮转。面向 3 核 20G Linux 主机，服务限额 2 CPU / 2G；默认仅回环监听，通过 SSH 隧道访问。备份、迁移、恢复与回滚步骤已写入说明。

## 逐品种真实验证结果

下表为本次实际抓到的示例，不表示今日所有报价都已更新。全部单位为元/吨；精确口径、来源和抓取时间随价格入库，并保存在 `backend/tests/evidence/*.json`。这些文件仅用于验证，不会被生产服务加载充数。

| 顺序 | 品种 | 真实示例 | 报价日期 | 固定口径摘要与来源 |
| --- | --- | ---: | --- | --- |
| 1 | abs | 12090 | 2026-11-11 | 华南吉化（揭阳）0215A 现货参考价，[普拉司](https://www.plasway.com/news/detail/41914EE7D30619D634A8CCE041BC49E3) |
| 2 | pc | 14500 | 2026-11-11 | 华南利华益维远 WY-111BR 现货参考价，[普拉司](https://www.plasway.com/news/detail/C9255FC6460FDED28DE6E6F00147D018) |
| 3 | pmma | 14466.67 | 2026-11-09 | 生意社橡塑大宗榜现货基准价，非牌号成交价，[塑价365公开转载](https://www.pla365.com/pmma/) |
| 4 | lithium_carbonate | 89200 | 2026-11-12 | 电池级早盘 Li2CO3≥99.5% 现货中间价，[我的钢铁新能源](https://xny.mysteel.com/) |
| 5 | dmc | 13800 | 2026-11-11 | DMC 新料主流现货区间低端，税运未注明，[有机硅商城](https://www.yjgmall.com/news/show.php?itemid=13844) |
| 6 | silicone_rubber | 14800 | 2026-11-11 | 普通生胶主流现货区间低端，税运未注明，[有机硅商城](https://www.yjgmall.com/news/show.php?itemid=13844) |
| 7 | pe | 10280 | 2026-11-11 | 华南中石化海南 LLDPE DFDA 7042 现货参考价，[普拉司](https://www.plasway.com/news/detail/A4388C00DA89FE17F36E0971E04283EB) |
| 8 | pp | 11500 | 2026-11-11 | 华南福建中景石化 T30S 拉丝粒料现货参考价，[普拉司](https://www.plasway.com/news/detail/991B9DDC8F60623F814ED8E192BC6C31) |
| 9 | bopet | 10400 | 2026-11-11 | 江浙 12μ 印刷基膜现货商谈区间低端，[我的钢铁公开摘要](https://m.mysteel.com/hot/1647652.html) |
| 10 | paper | 2900 | 2026-11-11 | 上海兆美 140g/m² 瓦楞原纸、上海交货报价，[生意社交易平台](https://www.rawmex.cn/ppinews-864812.html) |

DMC/硅橡胶使用文章明确写出的“价格统计时间：11月11日”，没有把 11月12日发布日期充作报价日期；区间低端是原文数值，不计算或伪称均价。PMMA 使用结构化 `spot` 数值保留两位小数，不读取 `fut`，不使用页面顶部更新日。碳酸锂锁定早盘表的现货行，不读取旁边的 LC 合约升贴水表。纸张同时校验供应商、品牌、交货地、140g 克重与瓦楞原纸，不能替换为废纸、箱板纸或其他克重。

首次验证时生意社中文首页、SunSirs 返回安全检查，中塑在线返回 403，已停用这些研究入口，未执行校验 JS、未获取绕过 Cookie。闽纸汇证书过期，未关闭 TLS 校验。最后采用上表可正常访问的公开页面；未访问需要登录才能显示的价格。

## 全站自测清单与结果

本机使用现有 Python 3.12 虚拟环境与 Node 24.22.1（符合项目 Node 引擎范围）；容器构建配置为 Node 22。所有测试数字只写临时库；生产库只增加上述真实采集记录。测试脚本的暂停规则创建后均删除，既有规则内容保持不变。

| 检查项 | 执行方式与证据 | 结果 |
| --- | --- | --- |
| 10 品种真实来源逐一抓取、入库、回读 | 逐个运行 `backend/scripts/verify_stage4_source.py <code>`；详见进度与 JSON | 10/10 通过 |
| 一条命令生产构建并启动 | `python start.py --port 8010`；619 模块构建，单 worker 监听 | 通过 |
| 复用构建重启与数据保留 | `python start.py --skip-build --port 8010`；OpenAPI 版本 0.4.0，11/11 真实报价仍在 | 通过 |
| 单服务首页、资源、favicon | `smoke_stage4.py`、`smoke_frontend.py`；首页及 6 个 JS/CSS 资源 HTTP 200，favicon 200 | 通过 |
| 全品种价格与完整元数据 | `smoke_stage4.py`；code、price、unit、basis、date、source/name/URL、fetched_at 均校验 | 11/11 通过 |
| 7/30/90 天历史、升序、相同口径 | `smoke_stage4.py`；11 × 3 组请求，每组均含真实最新报价 | 33/33 通过 |
| 报价过期与缺前价 | 当前 2 个今日报价、9 个旧日期报价正确标记；新源仅有一个真实报价点时涨跌为 null | 通过 |
| 制造商/牌号/市场/单位/克重变化拒收 | 真实最小片段回归与变异测试；不接受隐藏价格、空页、未来日期或升贴水 | 通过 |
| 重复采集 upsert、不重复日线 | 临时 SQLite 连续两轮 10 新源，仍只有 10 个价格点 | 通过 |
| 单源失败隔离与保留旧价 | 临时库模拟 A00 受限，其余 10 源仍成功；再模拟 ABS 失败，旧价保留 | 通过 |
| 卓创休眠与日额度 | 假凭据的隔离测试仍不调用卓创；生产库额度记录 0；adapter 文件 diff 为空 | 通过 |
| 抓取互斥与 API 409 | 隔离 TestClient 持锁请求 `/api/fetch/run` 返回 409，无上游请求 | 通过 |
| 定时任务 | 隔离 lifespan 校验 daily_prices=北京时间15:30、alerts=300秒，max_instances=1 | 通过（未等待实际15:30触发） |
| 全品种提醒规则 CRUD | 运行中 API 为 11 品种创建/编辑/删除暂停规则；口径快照正确，原规则未改 | 11/11 通过 |
| 通道/推送日志读取、非法阈值 | 通道 configured、日志分页正常、阈值0返回422 | 通过 |
| Telegram 三种提醒与日去重回放 | `replay_alerts.py` 复用前三阶段真实 A00 历史，临时库隔离通道 | 通过：7次回放事件，未发 Telegram |
| 404/422、缺失资源、未知 API | `smoke_stage4.py`、`smoke.py` | 通过 |
| 后端全量测试 | `python -m pytest backend/tests -q` | **113 passed，25.28秒** |
| Vue 组件与 ECharts SVG | `npm test`，2文件8项；使用运行中服务导出的真实响应 | **8 passed** |
| 旧验收脚本兼容 | `smoke.py` 已改为11免费品种；`smoke_frontend.py` 重新导出真实页面响应 | 通过 |
| Compose 静态验证 | PyYAML 解析；核对两构建上下文、全部COPY输入、回环绑定、只读凭据、卷、限额、健康检查、日志轮转 | 通过（静态） |
| Git diff / 忽略规则 | `git diff --check` 无错误；`.env`、data 不在跟踪清单；原有未跟踪启动/提示文件未改 | 通过 |
| Docker config/build/up/容器健康/卷恢复 | 本机无 Docker CLI/daemon | **未运行，不能记通过** |
| 真实浏览器点击、移动端视觉和截图 | 浏览器 host unavailable；Chrome 控制返回 `Computer Use was not approved to use Google Chrome` | **未完成，环境限制** |
| 运行期间真实 Telegram 测试记录 | 收尾复核见 `/api/alerts/test` 被调用，推送日志 #5，2026-11-12 14:15:06，状态 sent；自动验收脚本未请求发送 | **已观察到1条真实sent记录；未核对目标会话收信** |
| 云服务器部署 | 用户限定本阶段只产出配置与文档 | **未执行** |

运行中验收详细输出保存在忽略提交的 `data/stage4-live.json`、`data/stage2-live.json`、`data/selftest-readonly.json`；回放为 `data/stage3-replay.json`，SVG 为 `data/stage2-qa/alu-a00-chart.svg`。原始公开响应缓存为 `data/stage4-evidence/`，最小解析片段与来源记录为 `backend/tests/evidence/`。后端测试有既有 Starlette/httpx 弃用提示；构建有 ECharts 约503kB的分包提示，均不导致失败。

## 验证限制与运行说明

现货源可能晚更新，某日无新报价时保留原日期并标记过期。此阶段每品种只有本次真实入库的单点，没有下载或拼接不同口径历史；未来按日积累后才自然出现涨跌比较。免费来源可用性不作长期保证，解析变化会如实报告失败。

逐品种验证脚本没有触发提醒服务；自动化本地服务检查没有调用 `/api/alerts/test`、`/api/alerts/check` 或真实全源 `/api/fetch/run`。但收尾检查运行日志发现 14:15:06 的 `/api/alerts/test` 请求，数据库推送日志 #5 状态为 sent；这是真实测试消息，不能归为隔离回放，触发方未由本次工具核定，目标会话收信未核对。该观察已补入 `data/stage4-live.json`。全源失败隔离、落库与额度逻辑通过临时库/真实片段覆盖，真实抓取则通过逐品种脚本覆盖。日调度由 lifespan 配置测试覆盖，没有谎称实际等到定时任务触发。

自测服务使用8010端口，验收后停止。用户日常仍可在项目根目录 `python start.py` 打开8000端口；详细安装、启动、自测、Docker命令全部在 README / DEPLOYMENT.md。阶段停止在第四阶段，未开展后续工作。

## Git 提交状态与补交文件清单

已找到本机 Git，并核实前三阶段提交。ABS 验证完成后尝试暂存，返回：

```text
fatal: Unable to create 'D:/Project/procurement-website/.git/index.lock': Permission denied
```

沙箱对 `.git` 仅有读权限且不允许提权，故本阶段 **没有创建任何 commit**，其余品种继续按顺序验证并记录，没有重复卡在提交操作。以下为按用户约定提供的补交清单；`.env`、`data/`、`.tools/`、`.venv/`、前端构建产物以及原有 PROMPTS/启动批处理/阶段提示词不在清单中。

建议按以下10个品种顺序分别提交；共享 `config.py` 和 `STAGE4_PROGRESS.md` 按品种分块暂存，公共抓取基础设施随ABS提交，公共回归/UI/部署文档另作收尾提交。不要直接 `git add .` 纳入原有未跟踪文件。

| 建议提交 | 该品种新增文件；共享配置和进度均按对应品种分块 |
| --- | --- |
| `feat: stage4 free ABS spot source` | `backend/app/adapters/abs.py`、`backend/app/adapters/plasway.py`、`backend/tests/evidence/abs.json`、`backend/tests/evidence/abs.html`；并带公共 `common.py`、`service.py`、`verify_stage4_source.py` |
| `feat: stage4 free PC spot source` | `backend/app/adapters/pc.py`、`backend/tests/evidence/pc.json`、`backend/tests/evidence/pc.html` |
| `feat: stage4 free PMMA spot source` | `backend/app/adapters/pmma.py`、`backend/tests/evidence/pmma.json`、`backend/tests/evidence/pmma.html` |
| `feat: stage4 free lithium carbonate spot source` | `backend/app/adapters/lithium_carbonate.py`、同名 `backend/tests/evidence/*.json`、`*.html` |
| `feat: stage4 free DMC spot source` | `backend/app/adapters/dmc.py`、`backend/app/adapters/yjgmall.py`、`backend/tests/evidence/dmc.json`、`backend/tests/evidence/yjgmall.html` |
| `feat: stage4 free silicone rubber spot source` | `backend/app/adapters/silicone_rubber.py`、`backend/tests/evidence/silicone_rubber.json` |
| `feat: stage4 free PE spot source` | `backend/app/adapters/pe.py`、`backend/tests/evidence/pe.json`、`backend/tests/evidence/pe.html`；共享 `plasway.py` 的PE表头变更 |
| `feat: stage4 free PP spot source` | `backend/app/adapters/pp.py`、`backend/tests/evidence/pp.json`、`backend/tests/evidence/pp.html` |
| `feat: stage4 free BOPET spot source` | `backend/app/adapters/bopet.py`、`backend/tests/evidence/bopet.json`、`backend/tests/evidence/bopet.html` |
| `feat: stage4 free corrugated paper spot source` | `backend/app/adapters/paper.py`、`backend/tests/evidence/paper.json`、`backend/tests/evidence/paper.html` |

公共/收尾补交文件（连同上表覆盖本阶段全部文件）：

- `backend/app/config.py`、`backend/app/service.py`、`backend/app/adapters/common.py`、`backend/app/main.py`
- `backend/app/adapters/plasway.py`、`backend/app/adapters/yjgmall.py`、上表10个品种adapter
- `backend/scripts/verify_stage4_source.py`、`backend/scripts/smoke_stage4.py`、`backend/scripts/smoke.py`
- `backend/tests/conftest.py`、`backend/tests/test_pipeline.py`、`backend/tests/test_stage4.py`、`backend/tests/evidence/README.md`、上表JSON和HTML片段
- `frontend/src/views/MarketView.vue`、`frontend/src/views/DetailView.vue`、`frontend/tests/pages.test.js`
- `docker-compose.yml`、`DEPLOYMENT.md`、`README.md`、`STAGE4_PROGRESS.md`、`STAGE4_REPORT.md`
