# 前端审计分项证据（2026-11-12）

本文件供根目录 AUDIT_REPORT.md 汇总。未修改业务源码、依赖清单或锁文件。新增审计用例只使用内存响应，不请求真实后端，不发推送。Node v24.22.1，npm 12.2.2，Vitest 4.1.12。

## 实际执行

项目根目录执行：

```powershell
node frontend/node_modules/vitest/vitest.mjs run --config backend/scripts/audit_frontend.config.mjs --reporter=verbose --no-color
```

原始输出在 `backend/scripts/audit_frontend_output.txt`：

```text
F1 after delayed GET: server_email=new@example.com; displayed_email=old@example.com; notice=个人邮箱已保存。
F1 second submit: server_email=old@example.com (new value overwritten)
F2 failed page=2: footer=第 2 页; rows=PAGE_ONE_MESSAGE (page 1); alert=HTTP 503
XSS: inserted_html_elements=0; rejected_protocols=javascript,data,file,vbscript
Test Files  1 passed (1)
Tests  3 passed (3)
```

注意：F1/F2 用例成功表示复现到了现存缺陷，并非已修复。

## F1【低】邮箱设置初始慢请求覆盖已保存输入

- 位置：`frontend/src/views/PushSettingsView.vue:5`、`:8`、`:11`、`:16`。
- 证据：初始 `load()` 不置 busy，输入框与保存按钮可以立即操作；初始 GET 尚未返回时保存 `new@example.com` 成功，再让 GET 的旧快照 `old@example.com` 返回，输入框变回旧邮箱但仍显示「个人邮箱已保存」。再次保存实际把服务器邮箱改回旧值。输出见上方 F1。
- 影响：慢网络下误导用户、丢失新设置；可能将后续通知继续发给旧邮箱。只影响本账号配置，没有证明跨账号越权。
- 建议：初始加载期间禁用提交；用请求序号/dirty 标记防止旧读取覆盖编辑；保存成功后以当前写入响应同步 email，并废弃先前读取。

## F2【低】日志翻页失败后页码与数据不一致

- 位置：`frontend/src/views/PushLogsView.vue:7`、`:10`、`:16`、`:28`。
- 证据：`turn()` 先增加 `page`，请求失败保留旧日志但没有回滚页码。模拟第 1 页有 31 条总计，第 2 页返回 503：页脚显示第 2 页，表格仍是第 1 页数据，下一页按钮因 `page * 30 >= total` 被禁用。输出见 F2。
- 影响：用户会把旧记录误认成新页内容，错误估计推送结果；虽有 HTTP 503 提示，记录本身没有旧页标记。
- 建议：请求成功后一起提交 page/logs/total，失败保持原页；或清空表格并显示失败页状态。

## F3【中；开发依赖】锁定依赖包含两个已知可用性漏洞

- 位置：`frontend/package-lock.json:1413`（@vue/test-utils 2.5.1）、`:1630`（editorconfig 3.0.2）、`:1759`（glob 13.0.7）、`:1832`（js-beautify 2.0.3）、`:1927`（minimatch 10.2.6）。
- 证据：在 frontend 目录执行 `node ../.tools/npm/package/bin/npm-cli.js audit --json --fetch-timeout=20000 --fetch-retries=0`，退出码 1，完整结果 `backend/scripts/audit_npm.json`：

```json
{"info":0,"low":0,"moderate":5,"high":0,"critical":0,"total":5}
```

这 5 项是 2 个根漏洞与依赖链传播的计数，并不是 5 个独立漏洞。`npm audit --omit=dev --json` 退出码 0，完整结果 `backend/scripts/audit_npm_production.json`：

```json
{"info":0,"low":0,"moderate":0,"high":0,"critical":0,"total":0}
```

- 影响：glob 取消后继续遍历、minimatch 恶意模式放大可导致测试/构建环境 CPU/IO/内存消耗；当前链路来自测试工具，不属于生产浏览器运行依赖，未发现网站接口可让远程用户传入 glob 模式。不能据此称生产网站存在直接远程 DoS。
- 建议：后续单独升级测试依赖及锁文件，使 glob 至少 14.0.2、minimatch 至少 11.0.1，并验证上层依赖兼容性；不要本阶段运行 audit fix。
- 官方依据：[glob 维护者公告 GHSA-3g3m-2fjr-6pw4](https://github.com/isaacs/node-glob/security/advisories/GHSA-3g3m-2fjr-6pw4)、[minimatch 维护者公告 GHSA-g3hv-75wg-m249](https://github.com/isaacs/minimatch/security/advisories/GHSA-g3hv-75wg-m249)。已在本次审计实时读取。

## 已查，未发现

- 前端 XSS：扫描 `frontend/src` 无 `v-html`、`innerHTML`、`outerHTML`、`eval`、动态 Function。将 `<img ... onerror=...>` 作为规则名称/口径/状态渲染，DOM 中 img 数量为 0，作为文本显示。来源链接只接受 http/https，危险协议四类实际拒绝；新窗口链接使用 `rel="noopener noreferrer"`。图表 formatter 的单位来自服务器固定品种配置，当前没有用户可控 HTML 通道。
- 权限入口：`router.js:11-12,17-22` 管理页面标记 admin 且以 `/api/auth/me` 返回角色判定；`App.vue` 管理导航与数据源面板按角色隐藏；`store.js` 普通账号不会请求全局源接口。真正授权仍以主审计的后端对抗测试为准。
- 账号状态：登录/退出/改密成功均整页跳转，正常单标签切换会重建组件内存；未发现 localStorage/sessionStorage 存令牌或私密配置。没有证据证明前端可以使 B 获得 A 的接口资源。
- 历史图竞态：`DetailView.vue` 有 AbortController、requestId 和卸载取消，晚到响应不会覆盖新查询。全局行情刷新去重，失败提示明确保留上次数据。
- 输入：登录/注册提供长度与账号字符约束；邮箱 type=email 和最大 254 字符；规则数值带 min/max/step，服务端验证另由后端审计覆盖。
- 前端依赖严重/高危：本次 npm audit 实际结果均为 0；生产依赖所有等级均为 0。

## 新增文件

1. `backend/tests/audit_frontend.test.js`：3 个隔离审计用例。
2. `backend/scripts/audit_frontend.config.mjs`：运行该用例的独立 Vitest 配置。
3. `backend/scripts/audit_frontend_output.txt`：原始运行输出。
4. `backend/scripts/audit_npm.json`：全部 npm 依赖审计输出。
5. `backend/scripts/audit_npm_production.json`：仅生产依赖审计输出。
6. `backend/scripts/audit_frontend_findings.md`：本分项说明。
