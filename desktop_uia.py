from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Callable


class DesktopUiaError(RuntimeError):
    pass


class DesktopUiaCancelled(DesktopUiaError):
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
$textLimit = [Math]::Max(256, [Math]::Min([int]$request.textLimit, 16000))
$nativeTimeoutMs = [Math]::Max(1000, [Math]::Min([int]$request.nativeTimeoutMs, 25000))
$deadline = [DateTime]::UtcNow.AddMilliseconds($nativeTimeoutMs)
$visitLimit = [Math]::Max(100, $limit * 4)
$visitCount = 0
$queue = New-Object 'System.Collections.Generic.Queue[object]'
$queue.Enqueue([pscustomobject]@{ Element = $root; ParentIndex = $null; Depth = 0 })
$nodes = New-Object 'System.Collections.Generic.List[object]'
while ($queue.Count -gt 0 -and $nodes.Count -lt $limit -and $visitCount -lt $visitLimit -and [DateTime]::UtcNow -lt $deadline) {
    $visitCount += 1
    $entry = $queue.Dequeue()
    $element = $entry.Element
    $nodeIndex = $nodes.Count
    $nodeAdded = $false
    try {
        $current = $element.Current
        $rect = $current.BoundingRectangle
        $actions = New-Object 'System.Collections.Generic.List[string]'
        $actions.Add('raise')
        $value = $null
        $readOnly = $null
        $selected = $false
        $pattern = $null
        if (-not [bool]$current.IsPassword -and $element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
            $value = [string]$pattern.Current.Value
            if ($value.Length -gt 1024) { $value = $value.Substring(0, 1024) }
            $readOnly = [bool]$pattern.Current.IsReadOnly
            if (-not $readOnly) { $actions.Add('set_value') }
        }
        $textValuePattern = $null
        if ($null -eq $value -and -not [bool]$current.IsPassword -and $element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$textValuePattern)) {
            $textRange = $textValuePattern.DocumentRange
            $value = [string]$textRange.GetText(1024)
            $readOnlyAttribute = $textRange.GetAttributeValue([System.Windows.Automation.TextPattern]::IsReadOnlyAttribute)
            $readOnly = if ($readOnlyAttribute -is [bool]) { [bool]$readOnlyAttribute } else { $false }
            if (-not $readOnly -and ([string]$current.ClassName -eq 'Edit' -or [string]$current.ControlType.ProgrammaticName -eq 'ControlType.Edit')) {
                $actions.Add('set_value')
            }
        }
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) { $actions.Add('invoke') }
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
            $selected = [bool]$pattern.Current.IsSelected
            $actions.Add('select')
        }
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
            $actions.Add('expand'); $actions.Add('collapse')
        }
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) { $actions.Add('toggle') }
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$pattern)) {
            if ([bool]$pattern.Current.VerticallyScrollable) { $actions.Add('scroll_up'); $actions.Add('scroll_down') }
            if ([bool]$pattern.Current.HorizontallyScrollable) { $actions.Add('scroll_left'); $actions.Add('scroll_right') }
        }
        $pattern = $null
        if ($element.TryGetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern, [ref]$pattern)) { $actions.Add('scroll_into_view') }
        $nodes.Add([pscustomobject]@{
            Element = $element
            Index = $nodeIndex
            ParentIndex = $entry.ParentIndex
            Depth = [int]$entry.Depth
            Name = [string]$current.Name
            AutomationId = [string]$current.AutomationId
            ControlType = [string]$current.ControlType.ProgrammaticName
            ClassName = [string]$current.ClassName
            Enabled = [bool]$current.IsEnabled
            Offscreen = [bool]$current.IsOffscreen
            Focused = [bool]$current.HasKeyboardFocus
            IsPassword = [bool]$current.IsPassword
            Value = $value
            ReadOnly = $readOnly
            Selected = $selected
            SecondaryActions = @($actions)
            Rect = [ordered]@{
                left = [int][Math]::Round($rect.Left)
                top = [int][Math]::Round($rect.Top)
                right = [int][Math]::Round($rect.Right)
                bottom = [int][Math]::Round($rect.Bottom)
                width = [int][Math]::Round($rect.Width)
                height = [int][Math]::Round($rect.Height)
            }
        })
        $nodeAdded = $true
    } catch { }
    if ($nodeAdded) {
        try {
            $child = $walker.GetFirstChild($element)
            while ($null -ne $child -and [DateTime]::UtcNow -lt $deadline) {
                $queue.Enqueue([pscustomobject]@{ Element = $child; ParentIndex = $nodeIndex; Depth = ([int]$entry.Depth + 1) })
                $child = $walker.GetNextSibling($child)
            }
        } catch { }
    }
}

function Format-Node($node) {
    return [ordered]@{
        index = $node.Index
        parentIndex = $node.ParentIndex
        depth = $node.Depth
        name = $node.Name
        automationId = $node.AutomationId
        controlType = $node.ControlType
        className = $node.ClassName
        enabled = $node.Enabled
        offscreen = $node.Offscreen
        focused = $node.Focused
        isPassword = $node.IsPassword
        value = $node.Value
        readOnly = $node.ReadOnly
        selected = $node.Selected
        secondaryActions = @($node.SecondaryActions)
        rect = $node.Rect
    }
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
    $match = $matches[0]
    if (($request.PSObject.Properties.Name -contains 'expectedName') -and $match.Name -ne [string]$request.expectedName) {
        throw 'The observed UI Automation element is stale; its name changed.'
    }
    if (($request.PSObject.Properties.Name -contains 'expectedAutomationId') -and $match.AutomationId -ne [string]$request.expectedAutomationId) {
        throw 'The observed UI Automation element is stale; its automation id changed.'
    }
    if (($request.PSObject.Properties.Name -contains 'expectedControlType') -and $match.ControlType -ne [string]$request.expectedControlType) {
        throw 'The observed UI Automation element is stale; its control type changed.'
    }
    if (($request.PSObject.Properties.Name -contains 'expectedClassName') -and $match.ClassName -ne [string]$request.expectedClassName) {
        throw 'The observed UI Automation element is stale; its class changed.'
    }
    return $match
}

if ([string]$request.operation -eq 'inspect') {
    $items = @($nodes | ForEach-Object { Format-Node $_ })
    $focusedElement = $null
    $focusedNode = @($nodes | Where-Object { $_.Focused } | Select-Object -First 1)
    if ($focusedNode.Count -gt 0) { $focusedElement = Format-Node $focusedNode[0] }
    $selectedElements = @($nodes | Where-Object { $_.Selected } | ForEach-Object { Format-Node $_ })
    $documentText = ''
    $selectedText = ''
    foreach ($node in $nodes) {
        $textPattern = $null
        try {
            if ($node.Element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$textPattern)) {
                if (-not $documentText) { $documentText = [string]$textPattern.DocumentRange.GetText($textLimit) }
                if (-not $selectedText) {
                    $fragments = New-Object 'System.Collections.Generic.List[string]'
                    foreach ($range in @($textPattern.GetSelection())) {
                        $remaining = $textLimit - (($fragments -join '').Length)
                        if ($remaining -le 0) { break }
                        $fragments.Add([string]$range.GetText($remaining))
                    }
                    $selectedText = $fragments -join ''
                }
                if ($documentText -and $selectedText) { break }
            }
        } catch { }
    }
    [ordered]@{
        ok = $true
        operation = 'inspect'
        count = $items.Count
        truncated = ($queue.Count -gt 0 -or $visitCount -ge $visitLimit -or [DateTime]::UtcNow -ge $deadline)
        visitCount = $visitCount
        elements = $items
        tree = $items
        focusedElement = $focusedElement
        selectedElements = $selectedElements
        documentText = $documentText
        selectedText = $selectedText
    } | ConvertTo-Json -Compress -Depth 10
    exit 0
}

$target = Select-Target
if ([string]$request.operation -eq 'resolve') {
    [ordered]@{ ok = $true; operation = 'resolve'; element = (Format-Node $target) } | ConvertTo-Json -Compress -Depth 8
    exit 0
}

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
        if ($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) {
            if ($pattern.Current.IsReadOnly) { throw 'The target element is read-only.' }
            $pattern.SetValue([string]$request.value)
        } else {
            $pattern = $null
            if ($element.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pattern)) {
                $readOnlyAttribute = $pattern.DocumentRange.GetAttributeValue([System.Windows.Automation.TextPattern]::IsReadOnlyAttribute)
                if ($readOnlyAttribute -is [bool] -and [bool]$readOnlyAttribute) { throw 'The target element is read-only.' }
                $performed = 'keyboard_replace_required'
            } elseif ([string]$target.ClassName -eq 'Edit' -or [string]$target.ControlType -eq 'ControlType.Edit') {
                $performed = 'keyboard_replace_required'
            } else {
                throw 'The target element does not support ValuePattern or an editable text control fallback.'
            }
        }
        if (-not $performed) { $performed = 'set_value' }
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
        $action = ([string]$request.action).Trim().ToLowerInvariant().Replace(' ', '_')
        $pattern = $null
        if (($action -eq 'raise' -or $action -eq 'focus')) {
            $element.SetFocus(); $performed = 'raise'
        } elseif ($action -eq 'select' -and $element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
            $pattern.Select(); $performed = 'select'
        } elseif (($action -eq 'expand' -or $action -eq 'collapse') -and $element.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$pattern)) {
            if ($action -eq 'expand') { $pattern.Expand() } else { $pattern.Collapse() }
            $performed = $action
        } elseif ($action -eq 'toggle' -and $element.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) {
            $pattern.Toggle(); $performed = 'toggle'
        } elseif ($action -eq 'invoke' -and $element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
            $pattern.Invoke(); $performed = 'invoke'
        } elseif ($action -eq 'scroll_into_view' -and $element.TryGetCurrentPattern([System.Windows.Automation.ScrollItemPattern]::Pattern, [ref]$pattern)) {
            $pattern.ScrollIntoView(); $performed = 'scroll_into_view'
        } elseif (($action -in @('scroll_up', 'scroll_down', 'scroll_left', 'scroll_right')) -and $element.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$pattern)) {
            $horizontal = [System.Windows.Automation.ScrollAmount]::NoAmount
            $vertical = [System.Windows.Automation.ScrollAmount]::NoAmount
            if ($action -eq 'scroll_up') { $vertical = [System.Windows.Automation.ScrollAmount]::LargeDecrement }
            if ($action -eq 'scroll_down') { $vertical = [System.Windows.Automation.ScrollAmount]::LargeIncrement }
            if ($action -eq 'scroll_left') { $horizontal = [System.Windows.Automation.ScrollAmount]::LargeDecrement }
            if ($action -eq 'scroll_right') { $horizontal = [System.Windows.Automation.ScrollAmount]::LargeIncrement }
            $pattern.Scroll($horizontal, $vertical); $performed = $action
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
    element = (Format-Node $target)
} | ConvertTo-Json -Compress -Depth 8
"""


class WindowsUiaAdapter:
    def __init__(self, *, timeout_seconds: float = 15.0) -> None:
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))

    def execute(self, request: dict[str, Any], cancel_check: Callable[[], bool] | None = None) -> dict[str, Any]:
        bounded_request = dict(request)
        bounded_request.setdefault("nativeTimeoutMs", int(self.timeout_seconds * 800))
        payload = json.dumps(bounded_request, ensure_ascii=False, separators=(",", ":"))
        if len(payload.encode("utf-8")) > 64 * 1024:
            raise DesktopUiaError("UI Automation request exceeds the 64 KiB limit.")
        environment: dict[str, str] = {}
        for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"):
            value = str(os.environ.get(key) or "").strip()
            if value:
                environment[key] = value
        system_root = environment.get("SystemRoot") or environment.get("WINDIR") or r"C:\Windows"
        environment["PATH"] = os.pathsep.join([str(os.path.join(system_root, "System32")), str(system_root)])
        environment["VRCFORGE_UIA_REQUEST"] = payload
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", _POWERSHELL_UIA_SCRIPT],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise DesktopUiaError(f"UI Automation helper failed to run: {exc}") from exc
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancel_check is not None and cancel_check():
                process.terminate()
                try:
                    process.communicate(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=1)
                raise DesktopUiaCancelled("UI Automation was cancelled by the user.")
            if time.monotonic() >= deadline:
                process.kill()
                process.communicate(timeout=1)
                raise DesktopUiaError("UI Automation helper exceeded its native deadline.")
        output = stdout.strip()
        if process.returncode != 0:
            detail = (stderr.strip() or output or "unknown UI Automation error")[-1000:]
            raise DesktopUiaError(detail)
        if len(output.encode("utf-8")) > 1024 * 1024:
            raise DesktopUiaError("UI Automation response exceeds the 1 MiB limit.")
        try:
            result = json.loads(output)
        except json.JSONDecodeError as exc:
            raise DesktopUiaError("UI Automation helper returned invalid JSON.") from exc
        if not isinstance(result, dict) or not result.get("ok"):
            raise DesktopUiaError("UI Automation helper did not return a successful result.")
        return result

    def inspect(
        self,
        window_handle: int,
        *,
        limit: int = 300,
        text_limit: int = 16000,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self.execute(
            {
                "operation": "inspect",
                "windowHandle": int(window_handle),
                "limit": int(limit),
                "textLimit": int(text_limit),
            },
            cancel_check,
        )
