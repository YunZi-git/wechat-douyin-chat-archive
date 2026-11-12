$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

Write-Host "================================================"
Write-Host "  抖音聊天记录提取  (douyin-chat-export)"
Write-Host "================================================"
Write-Host "  浏览界面: http://127.0.0.1:8000"
Write-Host "  控制面板: http://127.0.0.1:8000/panel"
Write-Host ""
Write-Host "  关闭本窗口或按 Ctrl+C 即可停止服务。"
Write-Host "================================================"
Write-Host ""

& $py -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
Write-Host ""
Write-Host "服务已停止。"
Read-Host "按回车键关闭窗口" | Out-Null
