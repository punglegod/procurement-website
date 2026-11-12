# pip-audit 复核与适用性（2026-11-12）

结论：后端业务依赖的解析审计为 **27 个包、0 个已知漏洞**。当前项目 `.venv` 环境为 **35 个包，只有 pip 25.0.1 报告漏洞**；原始 12 条记录去重后是 **6 个 GHSA/CVE，每个重复 2 次**。这是安装工具链风险，不能写成 12 个不同后端运行时漏洞。没有发现由网站请求直接调用 pip 的路径。

## 已核对的原始文件

- `backend/scripts/audit_pip_requirements.json:1`：27 个解析依赖，所有 `vulns` 为空。
- `backend/scripts/audit_pip_environment.json:1`：35 个已安装包，pip 25.0.1 包含 12 条记录，其他包为空。
- `.venv/Lib/site-packages/pip-25.0.1.dist-info/METADATA:3`：`Version: 25.0.1`。
- `.venv/Lib/site-packages/pip/_internal/utils/unpacking.py:183-190,209,245`：存在 `tarfile.data_filter` 时不使用旧 fallback。
- `.venv/Lib/site-packages/pip/_internal/utils/unpacking.py:78-86`：旧路径边界用字符串 `commonprefix`。
- `.venv/Lib/site-packages/pip/_internal/models/link.py:408-419`：filename 对 URL 文件名再次 unquote。

## 实际运行探针

命令：

```powershell
.\.venv\Scripts\python.exe backend/scripts/audit_pip_probe.py
```

实际输出（完整文件：`backend/scripts/audit_pip_probe_output.txt`）：

```text
python=3.12.14; pip=25.0.1; tarfile.data_filter=True
environment_dependencies=35; raw_findings=12; unique_GHSA=6
duplicate_counts={"GHSA-4xh5-x5gv-qwph": 2, "GHSA-58qw-9mgm-455v": 2, "GHSA-6vgw-5pg2-w6jp": 2, "GHSA-jp4c-xjxw-mgf9": 2, "GHSA-qwm4-qh6w-59xr": 2, "GHSA-wf93-45jw-7689": 2}
requirements_dependencies=27; requirements_findings=0
CVE-2025-8869 tar_symlink_rejected=True; outside_created=False
CVE-2026-1703 sibling_prefix_accepted=True; actually_inside=False
CVE-2026-13346 doubly_encoded_filename='../probe.whl'; contains_separator=True
```

探针只读取已有 JSON、调用安装工具的纯路径函数，并在自动清理的临时目录中验证 tar 过滤。没有安装恶意包、修改 pip、升级依赖或连接网络。CVE-1703/13346 的输出证实底层脆弱判断存在，不表示已通过网站发起利用。

## 去重、严重性与 Python 3.12 条件

以下级别依据 GHSA 审核分类，并用 Python 安全公告 / pip 维护者说明核对技术条件和修复；本次读取的 6 项没有高危或严重级别。修复版本也与 pip 官方 [changelog](https://pip.pypa.io/en/stable/news/) 交叉核对。

| CVE / GHSA | 公告级别 | 修复版本 | 在本项目当前环境的适用性与触发条件 |
| --- | --- | --- | --- |
| CVE-2025-8869 / GHSA-4xh5-x5gv-qwph | 中 | 25.3 | **当前 Python 3.12.14 不适用脆弱 fallback**：`tarfile.data_filter=True`，实际越界符号链接被拒绝。官方说明 Python >=3.12 提供 PEP 706；仅包版本扫描未表达这个条件。[Python 官方公告](https://mail.python.org/archives/list/security-announce@python.org/thread/IF5A3GCJY3VH7BVHJKOWOJFKTW7VFQEN/) |
| CVE-2026-1703 / GHSA-6vgw-5pg2-w6jp | 低 | 26.0 | Python 3.12 不免疫；安装恶意 wheel 时，字符串前缀判断允许安装目录的相似前缀兄弟路径。官方指出一般情况下不能借此覆盖可执行文件。当前函数探针已复现错误接受。[Python 官方公告](https://mail.python.org/archives/list/security-announce@python.org/thread/WIEA34D4TABF2UNQJAOMXKCICSPBE2DJ/) |
| CVE-2026-3219 / GHSA-58qw-9mgm-455v | 中 | 26.1 | Python 3.12 不免疫；需安装精心构造的 tar/ZIP 拼接归档，引起归档类型与实际安装内容不一致。[Python 官方公告](https://mail.python.org/archives/list/security-announce@python.org/thread/QAJ5JIVWWCAJ4EZL2FP5MOOW35JS7LRJ/) |
| CVE-2026-6357 / GHSA-jp4c-xjxw-mgf9 | 中 | 26.1 | Python 3.12 不免疫；恶意 wheel 覆盖后续 pip self-check 延迟导入的模块后，安装结束阶段可能执行代码。维护者说明修复为先做 self-check 再安装。[pip 维护者说明](https://sichard.ca/blog/2026/04/whats-new-in-pip-26.1/#fix-ace-caused-by-self-check-deferred-imports-cve-2026-6357) |
| CVE-2026-8643 / GHSA-wf93-45jw-7689 | 中 | 26.1.2 | Python 3.12 不免疫；安装恶意包时，console_scripts/gui_scripts 入口名可被当成路径，向脚本目录外写入。[Python 官方公告](https://mail.python.org/archives/list/security-announce@python.org/thread/YV63UET5D3OOJY7O4M5XCVYO2YM4NBYJ/) |
| CVE-2026-13346 / GHSA-qwm4-qh6w-59xr | 中 | 26.2.0 | Python 3.12 不免疫；需使用恶意包索引，单独上传恶意包不足以利用。官方特别指出对 `pip download --only-binary` 的安全边界有实际影响。当前 Link.filename 探针产生 `../probe.whl`。[Python 官方公告](https://mail.python.org/archives/list/security-announce@python.org/thread/L2BNQGGVQCEV7DROOORQ7WFKKFF2OOQX/) |

因此，按公告原始分类为 **5 中 + 1 低**；排除当前 Python 已规避的 CVE-2025-8869 后，剩余 **4 中 + 1 低，全部需要工具链操作及恶意输入条件**。不应把这些风险记为匿名 HTTP 请求即可触发的站点漏洞。

## 建议报告条目：中——本地 pip 工具链版本过旧

- **位置**：上述 pip 安装元数据与函数位置；环境审计 JSON 第 1 行。pip 不是 `backend/requirements.txt` 中的业务依赖。
- **影响**：维护人员后续下载/安装恶意分发包或使用恶意索引时，可能写出安装目录、加载非预期代码或得到非预期归档内容。服务请求路径没有 pip 调用，单纯访问网站不会触发这组安装工具漏洞。
- **修复建议**：在用户批准的修复阶段，把构建/开发安装工具更新到至少覆盖以上修复的 pip 26.2.0 或更新的兼容维护版；复跑环境与 requirements 两种审计。继续使用可信索引，避免以高权限安装不可信包。
- **边界**：当前 `.venv` 的 pip25.0.1 不能代表 `python:3.12-slim-bookworm` 镜像中的实际 pip 版本。Docker 镜像尚未构建/检查，不据此判断镜像已受影响；之后应审计最终镜像。
- **本阶段操作**：仅复核报告和运行探针，未安装升级、未更改业务文件或依赖清单。
