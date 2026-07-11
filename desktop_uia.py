from __future__ import annotations

import json
import os
import subprocess
from typing import Any


class DesktopUiaError(RuntimeError):
    pass


_POWERSHELL_UIA_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$request = $env:VRCFORGE_UIA_REQUEST | ConvertFrom-Json
$root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$request.windowHandle)
if ($null -eq $root) { throw 'UI Automation could not resolve the target window.' }
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$limit = [Math]::Max(1, [Math]::Min([int]$request.limit, 500))
$queue = New-Object 'System.Collections.Generic.Queue[System.Windows.Automation.AutomationElement]'
$queue.Enqueue($root)
$nodes = New-Object 'System.Collections.Generic.List[object]'
while ($queue.Count -gt 0 -and $nodes.Count -lt $limit) {
    $element = $queue.Dequeue()
    try {
        $current = $element.Current
        $rect = $current.BoundingRectangle
        $nodes.Add([pscustomobject]@{
            Element = $element
            Index = $nodes.Count
            Name = [string]$current.Name
            AutomationId = [string]$current.AutomationId
            ControlType = [string]$current.ControlType.ProgrammaticName
            ClassName = [string]$current.ClassName
            Enabled = [bool]$current.IsEnabled
            Offscreen = [bool]$current.IsOffscreen
            Focused = [bool]$current.HasKeyboardFocus
            IsPassword = [bool]$current.IsPassword
            Rect = [ordered]@{
                left = [int][Math]::Round($rect.Left)
                top = [int][Math]::Round($rect.Top)
                width = [int][Math]::Round($rect.Width)
                height = [int][Math]::Round($rect.Height)
            }
        })
    } catch { }
    try {
        $child = $walker.GetFirstChild($element)
        while ($null -ne $child) {
            $queue.Enqueue($child)
            $child = $walker.GetNextSibling($child)
        }
    } catch { }
}

function Select-Target {
    if ($null -ne $request.elementIndex) {
        $matches = @($nodes | Where-Object { $_.Index -eq [int]$request.elementIndex })
    } else {
        $matches = @($nodes | Where-Object {
            (-not $request.automationId -or $_.AutomationId -eq [string]$request.automationId) -and
            (-not $request.name -or $_.Name -eq [string]$request.name) -and
            (-not $request.controlType -or $_.ControlType -eq [string]$request.controlType -or $_.ControlType -eq ('ControlType.' + [string]$request.controlType))
        })
    }
    if ($matches.Count -eq 0) { throw 'UI Automation element was not found.' }
    if ($matches.Count -gt 1) { throw 'UI Automation element target is ambiguous; include elementIndex or more exact selectors.' }
    return $matches[0]
}

if ([string]$request.operation -eq 'inspect') {
    $items = @($nodes | ForEach-Object {
        [ordered]@{
            index = $_.Index
            name = $_.Name
            automationId = $_.AutomationId
            controlType = $_.ControlType
            className = $_.ClassName
            enabled = $_.Enabled
            offscreen = $_.Offscreen
            focused = $_.Focused
            isPassword = $_.IsPassword
            rect = $_.Rect
        }
    })
    [ordered]@{ ok = $true; operation = 'inspect'; count = $items.Count; truncated = ($queue.Count -gt 0); elements = $items } | ConvertTo-Json -Compress -Depth 8
    exit 0
}

$target = Select-Target
$element = $target.Element
$performed = ''
switch ([string]$request.operation) {
    'focus' {
        $element.SetFocus()
        $performed = 'focus'
    }
    'set_value' {
        if ($target.IsPassword) { throw 'Refusing to set a password element through Computer Use.' }
        $pattern = $null
        if (-not $element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
            throw 'The target element does not support ValuePattern.'
        }
        if ($pattern.Current.IsReadOnly) { throw 'The target element is read-only.' }
        $pattern.SetValue([string]$request.value)
        $performed = 'set_value'
    }
    'invoke' {
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
            $pattern.Invoke()
            $performed = 'invoke'
        } elseif ($element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
            $pattern.Select()
            $performed = 'select'
        } else {
            throw 'The target element does not support InvokePattern or SelectionItemPattern.'
        }
    }
    'secondary_action' {
        $action = ([string]$request.action).Trim().ToLowerInvariant()
        $pattern = $null
        if ($action -eq 'select' -and $element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
            $pattern.Select(); $performed = 'select'
        } elseif (($action -eq 'expand' -or $action -eq 'collapse') -and $element.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
            if ($action -eq 'expand') { $pattern.Expand() } else { $pattern.Collapse() }
            $performed = $action
        } elseif ($action -eq 'invoke' -and $element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
            $pattern.Invoke(); $performed = 'invoke'
        } else {
            throw 'The requested secondary UI Automation action is unsupported by this element.'
        }
    }
    default { throw 'Unsupported UI Automation operation.' }
}
[ordered]@{
    ok = $true
    operation = [string]$request.operation
    performed = $performed
    element = [ordered]@{ index = $target.Index; name = $target.Name; automationId = $target.AutomationId; controlType = $target.ControlType }
} | ConvertTo-Json -Compress -Depth 6
"""


class WindowsUiaAdapter:
    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))

    def execute(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 64 * 1024:
            raise DesktopUiaError("UI Automation request exceeds the 64 KiB limit.")
        environment = dict(os.environ)
        environment["VRCFORGE_UIA_REQUEST"] = payload
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_UIA_SCRIPT],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=self.timeout_seconds,
                creationflags=creation_flags,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DesktopUiaError(f"UI Automation helper failed to run: {exc}") from exc
        output = completed.stdout.strip()
        if completed.returncode != 0:
            detail = (completed.stderr.strip() or output or "unknown UI Automation error")[-1000:]
            raise DesktopUiaError(detail)
        if len(output.encode("utf-8")) > 512 * 1024:
            raise DesktopUiaError("UI Automation response exceeds the 512 KiB limit.")
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise DesktopUiaError("UI Automation helper returned invalid JSON.") from exc
        if not isinstance(result, dict) or not result.get("ok"):
            raise DesktopUiaError("UI Automation helper did not return a successful result.")
        return result

    def inspect(self, window_handle: int, *, limit: int = 300) -> dict[str, Any]:
        return self.execute({"operation": "inspect", "windowHandle": int(window_handle), "limit": int(limit)})
