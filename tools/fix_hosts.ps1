# 修复被 hosts 屏蔽的 GitHub 访问
# 只删除「指向 127.0.0.1 / 0.0.0.0 且域名里带 github 或 githubusercontent」的行，
# 其它内容原样保留，改之前会自动备份。

$ErrorActionPreference = 'Stop'
$hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host '需要管理员权限：请右键「修复-GitHub访问.bat」选择“以管理员身份运行”。' -ForegroundColor Yellow
    Read-Host '按回车退出'
    exit 1
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "$hosts.bak-$stamp"
Copy-Item -LiteralPath $hosts -Destination $backup -Force

$lines = Get-Content -LiteralPath $hosts
$pattern = '^\s*(127\.0\.0\.1|0\.0\.0\.0)\s+[^\s#]*(github\.com|github\.io|githubusercontent\.com|githubassets\.com|githubapp\.com|github\.dev|gist\.github\.com)\s*$'
$kept = $lines | Where-Object { $_ -notmatch $pattern }
$removed = $lines.Count - $kept.Count

if ($removed -eq 0) {
    Write-Host 'hosts 里没有发现 GitHub 相关的屏蔽记录，不需要修改。' -ForegroundColor Green
} else {
    Set-ItemProperty -LiteralPath $hosts -Name IsReadOnly -Value $false -ErrorAction SilentlyContinue
    [System.IO.File]::WriteAllLines($hosts, [string[]]$kept)
    Write-Host "已删除 $removed 条 GitHub 屏蔽记录，备份在：$backup" -ForegroundColor Green
}

ipconfig /flushdns | Out-Null
Write-Host ''
try {
    $ip = (Resolve-DnsName github.com -Type A -ErrorAction Stop | Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
    Write-Host "现在 github.com 解析到：$ip" -ForegroundColor Cyan
} catch {
    Write-Host '解析测试失败，请手动打开浏览器试一下。' -ForegroundColor Yellow
}
Write-Host ''
Read-Host '按回车关闭'
