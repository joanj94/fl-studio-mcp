# Opens WebView2's debugging port for FL Studio however FL is started, so FL's own
# assistant tools (see "FL's own assistant" in README) work without fl_launch.
#
# Sets WebView2's AdditionalBrowserArguments policy for FL's program only (no other
# app's WebView2 is touched): HKLM\SOFTWARE\Policies\Microsoft\Edge\WebView2\
# AdditionalBrowserArguments, value "<FL's exe name>" = "--remote-debugging-port=<port>".
# Policies need administrator rights, so this asks for them (UAC) when it lacks them.
# Takes effect the next time FL starts.
#
#   .\scripts\enable_fl_assistant_port.ps1            # port 9222 (or FL_MCP_GOPHER_PORT)
#   .\scripts\enable_fl_assistant_port.ps1 -Remove    # take it out again

param(
    [int]$Port = $(if ($env:FL_MCP_GOPHER_PORT) { [int]$env:FL_MCP_GOPHER_PORT } else { 9222 }),
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$PolicyKey = "HKLM:\SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"

# FL's program: FL_MCP_FL_EXE, else the one FL's installer registered (as fl_launch does).
$Program = $env:FL_MCP_FL_EXE
if (-not $Program) {
    $Program = (Get-ItemProperty -LiteralPath "HKLM:\SOFTWARE\Image-Line\Shared\Paths" `
        -ErrorAction SilentlyContinue).'FL Studio'
}
if (-not $Program -or -not (Test-Path -LiteralPath $Program)) {
    Write-Host "FL Studio's program wasn't found; set FL_MCP_FL_EXE to it." -ForegroundColor Red
    exit 1
}
# FL's folder also holds the program FL starts when it runs scaled.
$Names = @((Split-Path -Leaf $Program)) + @(Get-ChildItem -LiteralPath (Split-Path $Program) `
    -Filter "FL64 (scaled).exe" | ForEach-Object { $_.Name }) | Select-Object -Unique

$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin) {
    Write-Host "Asking for administrator rights to set the policy..."
    $Arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
                   "-Port", $Port)
    if ($Remove) { $Arguments += "-Remove" }
    try {
        $Child = Start-Process powershell -Verb RunAs -ArgumentList $Arguments -Wait -PassThru
    } catch {
        Write-Host "Administrator rights weren't given (UAC prompt declined); nothing changed." `
            -ForegroundColor Yellow
        exit 1
    }
    exit $Child.ExitCode
}

if ($Remove) {
    foreach ($Name in $Names) {
        Remove-ItemProperty -LiteralPath $PolicyKey -Name $Name -ErrorAction SilentlyContinue
    }
    Write-Host "Removed the debugging port for $($Names -join ', ')."
    exit 0
}

# Only when missing: New-Item -Force on an existing key would drop other apps' values.
if (-not (Test-Path -LiteralPath $PolicyKey)) { New-Item -Path $PolicyKey -Force | Out-Null }
foreach ($Name in $Names) {
    New-ItemProperty -LiteralPath $PolicyKey -Name $Name -PropertyType String `
        -Value "--remote-debugging-port=$Port" -Force | Out-Null
}
Write-Host "FL Studio ($($Names -join ', ')) opens WebView2's debugging port $Port from its next start."
exit 0
