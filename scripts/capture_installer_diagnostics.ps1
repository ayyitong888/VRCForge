param(
    [Parameter(Mandatory = $true)][string]$EvidenceDir,
    [Parameter(Mandatory = $true)][string]$InstallerInputRoot,
    [int]$DurationSeconds = 200
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$evidence = [IO.Path]::GetFullPath($EvidenceDir)
$inputRoot = ([IO.Path]::GetFullPath($InstallerInputRoot)).TrimEnd('\') + '\'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$output = Join-Path $evidence 'installer-diagnostics.jsonl'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
function Redact([string]$value) {
    if ([string]::IsNullOrEmpty($value)) { return $value }
    return [regex]::Replace($value, '(?i)(token|secret|password|authorization|api[_-]?key)(\s*[=:]\s*|\s+)[^\s;]+', '$1=[redacted]')
}
function Sample {
    $all = @(Get-CimInstance Win32_Process)
    $roots = @($all | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($inputRoot, [StringComparison]::OrdinalIgnoreCase) })
    $ids = [Collections.Generic.HashSet[uint32]]::new()
    $roots | ForEach-Object { [void]$ids.Add([uint32]$_.ProcessId) }
    do {
        $added = $false
        foreach ($p in $all) { if ($ids.Contains([uint32]$p.ParentProcessId) -and $ids.Add([uint32]$p.ProcessId)) { $added = $true } }
    } while ($added)
    $processes = foreach ($p in $all | Where-Object { $ids.Contains([uint32]$_.ProcessId) }) {
        $titles = @(); $controls = @()
        try {
            $live = Get-Process -Id $p.ProcessId -ErrorAction Stop
            $condition = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$p.ProcessId)
            foreach ($window in [Windows.Automation.AutomationElement]::RootElement.FindAll([Windows.Automation.TreeScope]::Children, $condition)) {
                $titles += $window.Current.Name
                $controls += @($window.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition) | Select-Object -First 40 | ForEach-Object { @{ name = $_.Current.Name; text = $_.Current.ControlType.ProgrammaticName } })
            }
        } catch { }
        [ordered]@{ processName = $p.Name; pid = [int]$p.ProcessId; parentPid = [int]$p.ParentProcessId; executablePath = $p.ExecutablePath; elapsedSeconds = if ($p.CreationDate) { ([DateTime]::UtcNow - $p.CreationDate.ToUniversalTime()).TotalSeconds } else { $null }; windowTitles = $titles; commandLine = Redact $p.CommandLine; controls = $controls }
    }
    $pf = Join-Path $env:ProgramFiles 'VRCForge'; $pd = Join-Path $env:ProgramData 'VRCForge'
    [ordered]@{ capturedAt = [DateTime]::UtcNow.ToString('o'); installerInputRoot = $inputRoot; processes = @($processes); programFilesVrcForge = @{ exists = Test-Path -LiteralPath $pf; topLevel = if (Test-Path -LiteralPath $pf) { @(Get-ChildItem -LiteralPath $pf -Force | Select-Object -First 40 -ExpandProperty Name) } else { @() } }; programDataVrcForge = @{ exists = Test-Path -LiteralPath $pd } } | ConvertTo-Json -Depth 8 -Compress | Add-Content -LiteralPath $output -Encoding utf8
}
$deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(0, $DurationSeconds))
do { Sample; if ([DateTime]::UtcNow -lt $deadline) { Start-Sleep -Seconds 10 } } while ([DateTime]::UtcNow -lt $deadline)
