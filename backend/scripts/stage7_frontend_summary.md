# 第七阶段前端修复与验证

## 改动

- **H-01 邮箱归属验证界面**：个人设置区显示已保存邮箱的已验证/未验证状态。保存仅调用 PUT；用户单独点击发送验证码，并以 8 位数字确认。修改尚未保存时不能验证旧地址；更换邮箱后的响应会撤销旧验证状态、清空旧验证码。发送服务待配置、限流 429、无效验证码均有明确反馈；验证码失败或送达未知优先展示后端 message，同时兼容 detail。api.js 将 pending_verification 显示为「待验证邮箱」，日志页将 verification 类型显示为「邮箱验证」，不再出现「规则 #null」。日志页说明邮箱验证要求，测试邮件 403 展示后端原因。管理员 Telegram 按后端结果继续展示成功，不被前端邮箱状态禁用。
- **L-02 慢读取覆盖**：初始读取完成前禁用输入和保存，函数本身也拒绝提前提交。读取和写入均带请求序号；读取前后比较编辑版本，不覆盖新输入；组件卸载后忽略晚到响应。保存前捕获 trim 后的提交地址，成功后以该快照同步输入和已保存值；仅当响应邮箱与提交地址一致时接受其已验证状态，避免省略或错误回显地址导致状态串用。读取失败提供重新读取入口。
- **L-03 翻页状态**：页码、记录、总数只在整组读取成功后一起提交。失败保留上一页完整状态和重试按钮，手动刷新保持当前页。
- **M-06 开发依赖**：在 package.json 使用限定上层依赖的 overrides，将 js-beautify 的 glob 升至 14.0.5，将 editorconfig 的 minimatch 升至 11.0.8；package-lock.json 同步。glob 使用的 minimatch 也解析到 11.0.8。未执行 `npm audit fix --force`。

## 依赖兼容选择

npm registry 实查：最新 @vue/test-utils 2.5.1 仍依赖 js-beautify ^2.0.0，最新 js-beautify 2.0.3 仍要求 glob ^13.0.6；editorconfig 3.0.2 仍要求 minimatch ~10.2.4。因此仅重新解析原范围不会消除漏洞。

没有把 editorconfig 全面升到 4.0.0，其 Node >=22 要求会收窄项目现有 Node 20.19+ 支持。选择保留现有上层版本并对其使用的匹配库进行明确安全覆盖；glob 14.0.5、minimatch 11.0.8 的 engines 均覆盖项目支持范围。当前验证运行在 Node 24.22.1，未声称已在每个受支持 Node 主版本实际运行。

`stage7_dependency_compat.mjs` 验证上层实际使用的 globSync、editorconfig.parseSync/Minimatch、js-beautify HTML 和 CLI glob/editorconfig 组合，全部通过。该脚本只用临时文件，并在删除前校验临时目录真实路径边界。

## 验证结果

```text
frontend npm test: Test Files 4 passed (4); Tests 23 passed (23)
原有14项保持未改；新增邮箱流程/错误/重试/分页/状态标签/提交快照9项。
audit_frontend: Test Files 1 passed (1); Tests 3 passed (3)
F1 FIXED: initial_save_blocked=true; delayed_GET_preserves_edit=true; saved_and_displayed=new@example.com
F2 FIXED: failed_page=2; footer=第 1 页; rows=PAGE_ONE_MESSAGE; retry_enabled=true
XSS: inserted_html_elements=0; rejected_protocols=javascript,data,file,vbscript
npm run build: success, 626 modules transformed
npm audit: info=0 low=0 moderate=0 high=0 critical=0 total=0
npm audit --omit=dev: info=0 low=0 moderate=0 high=0 critical=0 total=0
dependency compatibility: glob14.0.5/minimatch11.0.8/editorconfig3.0.2; all 4 checks passed
```

构建仍有原 ECharts chunk 超过 500 kB 的体积提示，不影响构建成功；未为此进行本阶段以外的图表重构。

命令（项目根目录；npm 命令在 frontend 目录执行）：

```powershell
node ../.tools/npm/package/bin/npm-cli.js test -- --no-color
node ../.tools/npm/package/bin/npm-cli.js run build
node ../.tools/npm/package/bin/npm-cli.js audit --json --fetch-timeout=20000 --fetch-retries=0
node ../.tools/npm/package/bin/npm-cli.js audit --omit=dev --json --fetch-timeout=20000 --fetch-retries=0
node ../.tools/npm/package/bin/npm-cli.js ls glob minimatch editorconfig js-beautify --json
# 以下从项目根目录执行
node frontend/node_modules/vitest/vitest.mjs run --config backend/scripts/audit_frontend.config.mjs --reporter=verbose --no-color
node backend/scripts/stage7_dependency_compat.mjs
```

原始输出：`stage7_frontend_install.txt`、`stage7_frontend_tests.txt`、`stage7_frontend_audit_tests.txt`、`stage7_frontend_build.txt`、`stage7_npm_audit.json`、`stage7_npm_production.json`、`stage7_npm_dependency_tree.json`、`stage7_dependency_compat_output.txt`，均位于 backend/scripts。

改动文件：`frontend/src/views/PushSettingsView.vue`、`PushLogsView.vue`、`frontend/src/api.js`、`frontend/package.json`、`package-lock.json`、新增 `frontend/tests/push-verification.test.js`、修复断言更新 `backend/tests/audit_frontend.test.js`。未修改 README、后端业务逻辑、正式 .env/数据库或 Windows ACL；未提交 git。

新增测试均使用隔离响应，不向真实邮箱或 Telegram 发送消息。后端端点已与本次 main.py 声明的 GET/PUT、request、confirm 数据结构核对；真实服务集成验收由主任务汇总。
