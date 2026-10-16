# lock_workspace.ps1 — Windows 专用工作区零信任写隔离脚本
param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectRoot,
    [switch]$Lock,
    [switch]$Unlock,
    [switch]$Status
)

$ScriptPath = Join-Path $PSScriptRoot "lock_workspace.py"
if ($Lock) {
    python $ScriptPath $ProjectRoot --lock
} elseif ($Unlock) {
    python $ScriptPath $ProjectRoot --unlock
} elseif ($Status) {
    python $ScriptPath $ProjectRoot --status
} else {
    Write-Host "用法: .\lock_workspace.ps1 -ProjectRoot <路径> [-Lock | -Unlock | -Status]"
}
