# 在普通 PowerShell 中从项目目录执行：.\commit_stage3.ps1
# 本脚本只提交第三阶段文件，不纳入已有提示词、启动批处理、凭据或数据库。
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskGit = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $taskGit) {
    $taskGit = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\native\git\cmd\git.exe'
}
if (-not (Test-Path -LiteralPath $taskGit)) { throw '未找到 Git，请安装 Git 或加入 PATH。' }
$stagedFiles = & $taskGit diff --cached --name-only
if ($LASTEXITCODE -ne 0) { throw '无法读取 Git 暂存区。' }
if ($stagedFiles) { throw '暂存区已有其他改动，请先检查，避免合并进本阶段提交。' }
$stageFiles = @(
    '.env.example', 'README.md', 'STAGE3_REPORT.md', 'commit_stage3.ps1',
    'backend/app/config.py', 'backend/app/main.py', 'backend/app/models.py',
    'backend/app/service.py', 'backend/app/alerts.py',
    'backend/scripts/replay_alerts.py', 'backend/scripts/smoke_alerts.py',
    'backend/tests/test_alerts.py', 'frontend/src/App.vue', 'frontend/src/api.js',
    'frontend/src/main.js', 'frontend/src/style.css',
    'frontend/src/views/AlertsView.vue', 'frontend/src/views/PushLogsView.vue',
    'frontend/tests/alerts.test.js'
)
& $taskGit add -- @stageFiles
if ($LASTEXITCODE -ne 0) { throw '暂存失败，请检查 .git 目录的写权限。' }
& $taskGit diff --cached --check
if ($LASTEXITCODE -ne 0) { throw '提交前差异检查失败，请先检查暂存区。' }
& $taskGit commit -m 'feat: stage3 Telegram alerts and delivery logs'
if ($LASTEXITCODE -ne 0) { throw '提交失败，请按 Git 提示处理。' }
& $taskGit log -1 --oneline
