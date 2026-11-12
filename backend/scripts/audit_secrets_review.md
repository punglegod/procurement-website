# 密钥扫描命中人工复核

审计时点：2026-11-12；HEAD `e34b8eedafbeda4a52a81409ad3cc6ab3c20e8bd`。本文件不包含任何实际凭据值，也未在线尝试任何凭据。

结论：**500 条宽泛正则命中全部完成复核，未发现疑似真实密钥、密码或 token 泄漏。** 按 LF 行号和整行指纹去重后为 76 种代码/文档行。原扫描中 Telegram、Resend、OpenAI、GitHub token 形状和私钥头的命中均为 0；本次不能将普通文本关键词命中误判成泄漏。

命中分类计数（含重复）：空模板 44、文档/指令 106、公开 SDK/前端构建代码 8、运行时变量或随机生成 193、隔离测试/自测配置 120、配置报错/待配置占位文案 29；合计 500。补查带引号赋值共 333 处、18 种去重候选，均已核对为非真实凭据。

## 复核方法

- 重新读取原扫描报告对应的工作树文件及 Git 历史 blob；按原扫描的 `\n` 行号提取，避免 Python `splitlines()` 把日志中的其他换行符计入行号。
- 查看去除所有字符串字面量后的代码形态，结合临时目录、mock sender、测试断言和运行时变量来源，核对这些行是否包含真正的常量秘密。仅保存位置、类型、长度、指纹、布尔特征和脱敏代码形态。
- 对所有 Git 历史文本 blob 以及原扫描已有命中的工作树文件补跑带引号的字典/JSON 凭据赋值模式。这类写法不完全受原宽泛正则覆盖。补查得到 18 种去重候选，均为隔离测试常量、空值、模板属性、变量生成语句或公开 SDK JavaScript 字符串边界误匹配。
- 长压缩行另外核对匹配附近：卓创缓存 SDK 中 `Token` 是 URL 拼接/运行时结果变量，登录字段初始值为空；前端构建产物中密码来自 Vue 表单变量，ECharts 的 token 属于文本解析变量。未发现嵌入的用户登录结果或实际凭据。

## 代表性证据位置

| 分类 | 位置 | 复核结果 |
|---|---|---|
| 空凭据模板 | `.env.example:14`、`:21`；历史 blob `bfe60bde0047`、`e8623b56563b` | 赋值为空；宽泛正则的空白匹配跨到下一行变量名，因此产生命中 |
| 配置说明 | `AGENTS.md:28`、`:33`；`DEPLOYMENT.md:21`；`STAGE5_REPORT.md` | 描述 `.env` 键名与规矩，没有实际值 |
| 运行时令牌 | `backend/app/auth.py:67`、`:93`；历史 blob `d9b9f6506650` | 从 Cookie 读取或调用随机生成函数，非硬编码令牌 |
| 运行时配置 | `backend/app/db.py:54`；`backend/app/alerts.py:65` | 从 settings 字典读取；错误消息只写字段名 |
| 临时账号 | `backend/scripts/smoke_accounts.py:76`、`:78`、`:135`；历史 blob `c43567217b13` | 随机生成临时管理员密码；`.env` 内容使用运行时插值，未嵌入真实站长值 |
| 隔离测试 | `backend/tests/conftest.py:9`；`backend/tests/test_accounts.py:20`、`:322`；历史 blob `97c610aff6a0`、`e23a80782e36` | 明确的测试密码与测试更新值，临时 `.env` / SQLite 使用 |
| 旧卓创框架测试 | `backend/tests/test_pipeline.py`；历史 blob `5a13a9e1ca16`、`bd31d002d324`、`12dc7c083b2d` | Cookie/密码均为 mock fixture；只用于隔离 HTTPTransport/MockTransport 测试 |
| 通道测试 | `backend/tests/test_alerts.py`；历史 blob `bc8f60d4d041`、`975b6dcf443a`；`backend/tests/test_accounts.py` 邮件测试 | 假 token 与 mock key，发送器隔离，不是实际服务凭据 |
| 前端表单 | `frontend/src/views/AccountView.vue:7`；`frontend/src/views/AuthView.vue:11`；对应构建产物 | 读取 `oldPassword.value` / `password.value`，没有常量密码 |
| 日志内重复代码 | `codex-stage1.err.log` 至 `codex-stage5.err.log` | 主要为历史工具输出和补丁的重复；与上述模板/代码/测试内容一致 |
| 公开 SDK 缓存 | `data/sci99-login-sdk.html:1`；`codex-stage1.err.log:1453` | URL 拼接与空输入字段，未发现已登录会话值 |

具体逐行分类和原命中位置：`backend/scripts/audit_secrets_review_shapes.json`。

复跑命令：

```powershell
.\.venv\Scripts\python.exe backend/scripts/audit_secrets_review.py
```

## 范围限制

原扫描覆盖全部 refs/reflogs 可达的 17 个提交、151 个不同 blob 和 186 份工作树文本；当前 `.env` 已配置秘密的精确匹配为 0。本复核继承原扫描排除项（实际 `.env`、依赖目录、二进制 SQLite、大于 20MB 文件和审计新产物），不宣称已经排除无标识的任意高熵字符串、二进制数据或无法取得的远端已删除历史中的秘密。没有凭据有效性在线检查，也没有修改历史或配置。
