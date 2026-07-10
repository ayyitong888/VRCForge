param(
    [Parameter(Mandatory = $true)]
    [string]$Marker,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName PresentationFramework

$window = New-Object System.Windows.Window
$window.Title = "VRCForge Desktop Executor Fixture $Marker"
$window.Width = 520
$window.Height = 260
$window.ResizeMode = "NoResize"
$window.WindowStartupLocation = "CenterScreen"

$grid = New-Object System.Windows.Controls.Grid
foreach ($height in @(55, 70, 70)) {
    $row = New-Object System.Windows.Controls.RowDefinition
    $row.Height = New-Object System.Windows.GridLength($height)
    [void]$grid.RowDefinitions.Add($row)
}

$label = New-Object System.Windows.Controls.TextBlock
$label.Text = "Type the marker, then submit"
$label.FontSize = 18
$label.VerticalAlignment = "Center"
$label.HorizontalAlignment = "Center"
[System.Windows.Controls.Grid]::SetRow($label, 0)
[void]$grid.Children.Add($label)

$textBox = New-Object System.Windows.Controls.TextBox
$textBox.Name = "MarkerInput"
$textBox.FontSize = 18
$textBox.Margin = "30,10,30,10"
$textBox.VerticalContentAlignment = "Center"
[System.Windows.Controls.Grid]::SetRow($textBox, 1)
[void]$grid.Children.Add($textBox)

$button = New-Object System.Windows.Controls.Button
$button.Name = "SubmitButton"
$button.Content = "Submit proof"
$button.Width = 180
$button.Height = 40
$button.FontSize = 16
$button.HorizontalAlignment = "Center"
$button.VerticalAlignment = "Center"
[System.Windows.Controls.Grid]::SetRow($button, 2)
[void]$grid.Children.Add($button)

$writeState = {
    param([string]$Status)
    $payload = @{
        schema = "vrcforge.desktop_executor_fixture.v1"
        marker = $Marker
        status = $Status
        text = $textBox.Text
        timestamp = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json -Compress
    [System.IO.File]::WriteAllText($OutputPath, $payload, [System.Text.UTF8Encoding]::new($false))
}

$textBox.Add_TextChanged({ & $writeState "typing" })
$button.Add_Click({
    & $writeState "submitted"
    $window.DialogResult = $true
    $window.Close()
})
$window.Add_Closed({
    if (-not (Test-Path -LiteralPath $OutputPath)) {
        & $writeState "closed_without_input"
    }
})

$window.Content = $grid
[void]$window.ShowDialog()
