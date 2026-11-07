param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [Parameter(Mandatory = $true)][ValidateSet('install', 'upgrade', 'uninstall')][string]$Mode,
    [Parameter(Mandatory = $true)][ValidateRange(1, 900)][int]$TimeoutSeconds
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
if ($env:RUNNER_ENVIRONMENT -ne 'github-hosted') { throw 'Hosted disposable VM required.' }
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
$button = [Windows.Automation.ControlType]::Button
$checkbox = [Windows.Automation.ControlType]::CheckBox

function Find-Control($Window, $Type, [string]$Name) {
    $property = [Windows.Automation.PropertyCondition]
    $element = [Windows.Automation.AutomationElement]
    $conditions = @(
        $property::new($element::ControlTypeProperty, $Type)
        $property::new($element::IsEnabledProperty, $true)
    )
    if ($Name) { $conditions += $property::new($element::NameProperty, $Name) }
    $condition = [Windows.Automation.AndCondition]::new([Windows.Automation.Condition[]]$conditions)
    return $Window.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
}

function Wait-Control($Process, $Type, [string]$Name) {
    $owner = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ProcessIdProperty, $Process.Id)
    $seen = @()
    do {
        if ($Process.HasExited) { throw "Setup exited before reaching '$Name' ($($Process.ExitCode))." }
        try {
            $window = [Windows.Automation.AutomationElement]::RootElement.FindFirst([Windows.Automation.TreeScope]::Children, $owner)
            if ($window) {
                $control = Find-Control $window $Type $Name
                if ($control) { return $control }
                if (Find-Control $window $button 'Export logs') { throw 'Native setup reached its failure screen.' }
                $seen = @($window.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition) |
                    Where-Object { $_.Current.Name } | Select-Object -First 25 -ExpandProperty Current | ForEach-Object { $_.Name })
            }
        } catch [Windows.Automation.ElementNotAvailableException] {
            # WebView2 recreates accessibility elements as setup changes screens.
        }
        Start-Sleep -Milliseconds 300
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for setup control '$Name'. Visible accessibility names: $($seen -join ', ')"
}

function Invoke-Button($Process, [string]$Name) {
    $control = Wait-Control $Process $button $Name
    $control.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    Write-Output "Invoked $Name"
}

$owned = $null
try {
    $start = @{ FilePath = $Executable; WorkingDirectory = (Split-Path -Parent $Executable); PassThru = $true }
    if ($Mode -eq 'uninstall') { $start.ArgumentList = '--uninstall' }
    $owned = Start-Process @start
    if ($Mode -eq 'uninstall') {
        # The registered uninstaller hands off to its own temporary copy before removing Program Files.
        $parentId = $owned.Id
        $tempPrefix = Join-Path $env:TEMP 'VRCForge-uninstall-'
        do {
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $parentId" | Where-Object {
                $_.Name -eq 'Uninstall.exe' -and $_.ExecutablePath -and
                $_.ExecutablePath.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase) -and
                $_.CommandLine -match " --uninstall --wait-for-parent $parentId\z"
            })
            if ($children.Count -eq 1) {
                $owned = Get-Process -Id $children[0].ProcessId
                $null = $owned.Handle
                break
            }
            Start-Sleep -Milliseconds 300
        } while ([DateTime]::UtcNow -lt $deadline)
        if ($owned.Id -eq $parentId) { throw 'The registered uninstaller did not start its temporary copy.' }
        $keep = Wait-Control $owned $checkbox ''
        if ($keep.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern).Current.ToggleState -ne [Windows.Automation.ToggleState]::On) {
            throw 'Uninstaller did not default to keeping personal data.'
        }
        Invoke-Button $owned 'Uninstall'
        Invoke-Button $owned 'Done'
    } else {
        # The welcome or upgrade screen contains one checkbox before the optional custom panel opens.
        $agreement = Wait-Control $owned $checkbox ''
        $toggle = $agreement.GetCurrentPattern([Windows.Automation.TogglePattern]::Pattern)
        if ($toggle.Current.ToggleState -ne [Windows.Automation.ToggleState]::Off) { throw 'The agreement checkbox was not initially unchecked.' }
        $toggle.Toggle()
        $action = if ($Mode -eq 'install') { 'Install VRCForge' } else { 'Upgrade VRCForge' }
        Invoke-Button $owned $action
        $null = Wait-Control $owned $button 'Launch VRCForge'
        Write-Output 'Reached completed installation screen'
        Invoke-Button $owned 'Close'
    }
    if (-not $owned.WaitForExit(15000)) { throw 'Setup stayed open after its completion action.' }
    if ($owned.ExitCode -ne 0) { throw "Setup exited with $($owned.ExitCode)." }
} finally {
    if ($owned) {
        $log = Join-Path $env:TEMP "VRCForge-setup-$($owned.Id).log"
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 18 }
        if (-not $owned.HasExited) { Stop-Process -InputObject $owned -ErrorAction SilentlyContinue }
    }
}
