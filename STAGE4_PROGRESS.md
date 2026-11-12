# 第四阶段进度

本文件只追加进度。真实响应缓存放在忽略提交的 `data/stage4-evidence/`；旧报价保留原日期，前端按过期数据展示。生意社中文首页与 SunSirs 返回安全检查，中塑在线返回 403，已停止这些入口，不执行校验脚本、不绕限制。卓创 adapter 文件保持原样，调度层明确休眠，即使误填凭据也不请求。


## abs — 2026-11-12T13:56:24+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：普拉司网，https://www.plasway.com/news/detail/41914EE7D30619D634A8CCE041BC49E3
- 固定口径：普拉司华南市场 ABS 中石油吉化（揭阳）0215A 现货参考价。
- 示例价格：12090.0 元/吨；报价日期：2026-11-11；抓取时间：2026-11-12T13:56:24+08:00。
- 证据：`backend/tests/evidence/abs.json`；提交结果见 STAGE4_REPORT.md。

## pc — 2026-11-12T13:56:58+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：普拉司网，https://www.plasway.com/news/detail/C9255FC6460FDED28DE6E6F00147D018
- 固定口径：普拉司华南市场 PC 山东利华益维远 WY-111BR 现货参考价。
- 示例价格：14500.0 元/吨；报价日期：2026-11-11；抓取时间：2026-11-12T13:56:58+08:00。
- 证据：`backend/tests/evidence/pc.json`；提交结果见 STAGE4_REPORT.md。

## pmma — 2026-11-12T13:57:28+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：塑价365（原始来源：生意社），https://www.pla365.com/pmma/
- 固定口径：塑价365转载生意社橡塑大宗榜 PMMA 现货基准价（市场均价，非牌号成交价）。
- 示例价格：14466.67 元/吨；报价日期：2026-11-09；抓取时间：2026-11-12T13:57:27+08:00。
- 证据：`backend/tests/evidence/pmma.json`；提交结果见 STAGE4_REPORT.md。

## lithium_carbonate — 2026-11-12T13:58:07+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：我的钢铁新能源，https://xny.mysteel.com/
- 固定口径：我的钢铁新能源 电池级碳酸锂（早盘）Li2CO3≥99.5% 现货中间价。
- 示例价格：89200.0 元/吨；报价日期：2026-11-12；抓取时间：2026-11-12T13:58:07+08:00。
- 证据：`backend/tests/evidence/lithium_carbonate.json`；提交结果见 STAGE4_REPORT.md。

## dmc — 2026-11-12T14:00:08+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：有机硅商城，https://www.yjgmall.com/news/show.php?itemid=13844
- 固定口径：有机硅商城 DMC 新料现货主流报价区间低端（非均价，税运未注明）。
- 示例价格：13800.0 元/吨；报价日期：2026-11-11；抓取时间：2026-11-12T14:00:08+08:00。
- 证据：`backend/tests/evidence/dmc.json`；提交结果见 STAGE4_REPORT.md。

## silicone_rubber — 2026-11-12T14:00:26+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：有机硅商城，https://www.yjgmall.com/news/show.php?itemid=13844
- 固定口径：有机硅商城 普通生胶现货主流报价区间低端（非混炼胶，税运未注明）。
- 示例价格：14800.0 元/吨；报价日期：2026-11-11；抓取时间：2026-11-12T14:00:26+08:00。
- 证据：`backend/tests/evidence/silicone_rubber.json`；提交结果见 STAGE4_REPORT.md。

## pe — 2026-11-12T14:00:49+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：普拉司网，https://www.plasway.com/news/detail/A4388C00DA89FE17F36E0971E04283EB
- 固定口径：普拉司华南市场 LLDPE 中石化海南 DFDA 7042 现货参考价。
- 示例价格：10280.0 元/吨；报价日期：2026-11-11；抓取时间：2026-11-12T14:00:49+08:00。
- 证据：`backend/tests/evidence/pe.json`；提交结果见 STAGE4_REPORT.md。

## pp — 2026-11-12T14:01:07+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：普拉司网，https://www.plasway.com/news/detail/991B9DDC8F60623F814ED8E192BC6C31
- 固定口径：普拉司华南市场 PP 福建中景石化 T30S 拉丝粒料现货参考价。
- 示例价格：11500.0 元/吨；报价日期：2026-11-11；抓取时间：2026-11-12T14:01:07+08:00。
- 证据：`backend/tests/evidence/pp.json`；提交结果见 STAGE4_REPORT.md。

## bopet — 2026-11-12T14:02:00+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：我的钢铁（隆众公开摘要），https://m.mysteel.com/hot/1647652.html
- 固定口径：我的钢铁/隆众公开摘要 江浙 BOPET 12μ印刷基膜现货商谈区间低端。
- 示例价格：10400.0 元/吨；报价日期：2026-11-11；抓取时间：2026-11-12T14:02:00+08:00。
- 证据：`backend/tests/evidence/bopet.json`；提交结果见 STAGE4_REPORT.md。

## paper — 2026-11-12T14:03:43+08:00

- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。
- 来源：生意社交易平台，https://www.rawmex.cn/ppinews-864812.html
- 固定口径：生意社交易平台 上海兆美纸业 140g/m² 瓦楞原纸 上海交货现货报价。
- 示例价格：2900.0 元/吨；报价日期：2026-11-11；抓取时间：2026-11-12T14:03:43+08:00。
- 证据：`backend/tests/evidence/paper.json`；提交结果见 STAGE4_REPORT.md。

## 第四阶段收尾 — 2026-11-12

- 10 个新增品种全部真实验证完成，无跳过；全站11品种均有真实入库价。
- 后端113项、前端8项测试通过；11品种×3历史区间、暂停规则CRUD、来源状态与参数验收通过。
- 已产出 docker-compose.yml、DEPLOYMENT.md；未部署服务器。Docker运行和浏览器视觉验收受环境限制，未记作通过。
- `.git/index.lock` 写入被沙箱拒绝，未创建commit。按品种的补交文件清单与详细结果见 STAGE4_REPORT.md。

## 收尾观察 — 2026-11-12

- 服务运行期间观察到 `/api/alerts/test` 在14:15:06被调用，推送日志#5状态sent；自动验收脚本未请求发送，目标会话实际收信未核对。详见报告。
- 自测服务已停止；日常仍用 `python start.py` 启动。
