[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("ValidateDestination", "Prepare", "Extract")]
    [string]$Action,

    [Parameter(Mandatory = $true)]
    [string]$Version,

    [string]$PayloadUrl,

    [string]$ExpectedSha256,

    [UInt64]$ExpectedLength,

    [string]$ProgramFilesRoot,
    [string]$StatePath,
    [string]$StageRoot,
    [string]$PayloadPath,
    [string]$DestinationRoot,

    [string]$ExpectedInstallLeaf = "VRCForge",
    [string]$StateTag = "VRCForge"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Keep these limits deliberately below Int32.MaxValue: .NET Framework's ZIP
# implementation and this bootstrapper must not accept unbounded archives.
[UInt64]$script:MaxArchiveBytes = 2147483648
[UInt64]$script:MaxEntryBytes = 536870912
[UInt64]$script:MaxExtractedBytes = 4294967296
[int]$script:MaxEntryCount = 4096
$script:StageParentName = "VRCForge Installer Staging"
$script:InstallSiblingPrefix = "VRCForge"
$script:PayloadFileName = "payload.zip"
$script:StateSchema = "vrcforge.web_payload_state.v1"
$script:SystemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
$script:AdministratorsSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
$script:TrustedInstallerSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464")
$script:CreatorOwnerSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-3-0")
$script:OwnerRightsSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-3-4")

function Fail([string]$Message) { throw "VRCForge web payload: $Message" }

function Get-ExpectedUrl {
    if ($Version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$') {
        Fail "Version has an invalid release form."
    }
    return "https://github.com/ayyitong888/VRCForge/releases/download/v$Version/VRCForge_Windows_x64_$Version.zip"
}

function Assert-Inputs {
    Get-TrustedProgramFilesRoot | Out-Null
    if ($ExpectedInstallLeaf -cne $StateTag) {
        Fail "ExpectedInstallLeaf and StateTag must be the same scoped identity."
    }
    if ($ExpectedInstallLeaf -ceq "VRCForge") {
        $script:StageParentName = "VRCForge Installer Staging"
        $script:InstallSiblingPrefix = "VRCForge"
    } elseif ($ExpectedInstallLeaf -cmatch '^VRCForge-Smoke-[a-f0-9]{32}$') {
        $script:StageParentName = "VRCForge Installer Staging-$ExpectedInstallLeaf"
        $script:InstallSiblingPrefix = $ExpectedInstallLeaf
    } else {
        Fail "ExpectedInstallLeaf is not a permitted production or smoke identity."
    }
    if ($Action -eq "ValidateDestination") { return }
    $expectedUrl = Get-ExpectedUrl
    if (-not [string]::Equals($PayloadUrl, $expectedUrl, [StringComparison]::Ordinal)) {
        Fail "PayloadUrl is not the exact version-bound official release URL."
    }
    if ($ExpectedSha256 -notmatch '^[A-Fa-f0-9]{64}$') { Fail "ExpectedSha256 must be SHA-256 hex." }
    if ($ExpectedLength -eq 0 -or $ExpectedLength -gt $script:MaxArchiveBytes) {
        Fail "ExpectedLength is outside the permitted archive limit."
    }
}

function Get-TrustedProgramFilesRoot {
    $expected = if ([string]::IsNullOrWhiteSpace($env:ProgramW6432)) { $env:ProgramFiles } else { $env:ProgramW6432 }
    $actual = Get-FullPath $ProgramFilesRoot
    if ([string]::IsNullOrWhiteSpace($expected) -or $actual -cne (Get-FullPath $expected)) { Fail "ProgramFilesRoot is not the native Program Files directory." }
    return Assert-NoReparsePath $actual
}

function Get-FullPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { Fail "A required path was empty." }
    return [IO.Path]::GetFullPath($Path)
}

function Assert-NoReparsePath([string]$Path, [switch]$AllowMissingLeaf) {
    $fullPath = Get-FullPath $Path
    $probe = $fullPath
    if ($AllowMissingLeaf) { $probe = [IO.Path]::GetDirectoryName($fullPath) }
    while (-not [string]::IsNullOrWhiteSpace($probe)) {
        if ([IO.Directory]::Exists($probe) -or [IO.File]::Exists($probe)) {
            if (([IO.File]::GetAttributes($probe) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                Fail "A path component is a reparse point: $probe"
            }
        }
        $parent = [IO.Directory]::GetParent($probe)
        if ($null -eq $parent -or $parent.FullName -eq $probe) { break }
        $probe = $parent.FullName
    }
    return $fullPath
}

function Assert-ContainedPath([string]$Root, [string]$Candidate) {
    $fullRoot = (Get-FullPath $Root).TrimEnd('\')
    $fullCandidate = Get-FullPath $Candidate
    if (-not $fullCandidate.StartsWith($fullRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        Fail "A path escaped its intended root."
    }
    return $fullCandidate
}

function Assert-NoUntrustedWriteAcl([string]$Path) {
    $trustedOwners = @($script:SystemSid.Value, $script:AdministratorsSid.Value, $script:TrustedInstallerSid.Value)
    $trustedWriters = $trustedOwners + @($script:CreatorOwnerSid.Value, $script:OwnerRightsSid.Value)
    # GenericAll | GenericWrite plus every concrete filesystem mutation right.
    [int64]$writeRights = 0x500D0156L
    $acl = [IO.Directory]::GetAccessControl($Path, [System.Security.AccessControl.AccessControlSections]::Owner -bor [System.Security.AccessControl.AccessControlSections]::Access)
    if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -notin $trustedOwners) { Fail "A destination path component has an untrusted owner: $Path" }
    foreach ($rule in @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))) {
        $rights = ([int64][int]$rule.FileSystemRights) -band 0xffffffffL
        if ($rule.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and ($rights -band $writeRights) -ne 0 -and $rule.IdentityReference.Value -notin $trustedWriters) {
            Fail "A destination path component grants an untrusted principal write access: $Path"
        }
    }
}

function Assert-SafeProgramFilesDestination([string]$Path) {
    $programFiles = Get-TrustedProgramFilesRoot
    $destination = Assert-ContainedPath $programFiles (Get-FullPath $Path)
    $parent = [IO.Directory]::GetParent($destination)
    if ($null -eq $parent -or -not [string]::Equals($parent.FullName, $programFiles, [StringComparison]::OrdinalIgnoreCase)) { Fail "The installation directory must be a direct child of Program Files." }
    if (-not [string]::Equals([IO.Path]::GetFileName($destination), $ExpectedInstallLeaf, [StringComparison]::Ordinal)) { Fail "The installation directory name does not match the exact scoped identity." }
    if (-not [IO.Directory]::Exists($destination)) { return $destination }
    $probe = $destination
    while ($true) {
        Assert-NoReparsePath $probe | Out-Null
        Assert-NoUntrustedWriteAcl $probe
        if ($probe -ceq $programFiles) { break }
        $parent = [IO.Directory]::GetParent($probe)
        if ($null -eq $parent) { Fail "Destination escaped ProgramFiles." }
        $probe = $parent.FullName
    }
    return $destination
}

function Get-InstallSiblingPath([string]$Kind) {
    $programFiles = Get-TrustedProgramFilesRoot
    Assert-NoUntrustedWriteAcl $programFiles
    for ($attempt = 0; $attempt -lt 8; $attempt++) {
        $candidate = Join-Path $programFiles ("$script:InstallSiblingPrefix-$Kind-" + [Guid]::NewGuid().ToString("N"))
        if (-not [IO.Directory]::Exists($candidate) -and -not [IO.File]::Exists($candidate)) {
            return $candidate
        }
    }
    Fail "Could not allocate an installation $Kind sibling."
}

function Assert-InstallSibling([string]$Path, [string]$Kind) {
    $programFiles = Get-TrustedProgramFilesRoot
    $fullPath = Assert-ContainedPath $programFiles $Path
    $parent = [IO.Directory]::GetParent($fullPath)
    if ($null -eq $parent -or -not [string]::Equals($parent.FullName, $programFiles, [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($fullPath) -notmatch ("^" + [regex]::Escape($script:InstallSiblingPrefix) + "-" + [regex]::Escape($Kind) + "-[a-f0-9]{32}$")) {
        Fail "Installation sibling is outside the allowed boundary."
    }
    Assert-NoReparsePath $fullPath | Out-Null
    Assert-NoUntrustedWriteAcl $fullPath
    return $fullPath
}

function Assert-NoReparseTree([string]$Root) {
    $root = Assert-NoReparsePath $Root
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $pending.Push($root)
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            Fail "An installation tree entry is a reparse point: $current"
        }
        if ([IO.Directory]::Exists($current)) {
            foreach ($child in [IO.Directory]::EnumerateFileSystemEntries($current)) {
                $pending.Push($child)
            }
        }
    }
}

function Remove-InstallSibling([string]$Path, [string]$Kind) {
    if (-not [IO.Directory]::Exists($Path)) { return }
    $fullPath = Assert-InstallSibling $Path $Kind
    Assert-NoReparseTree $fullPath
    [IO.Directory]::Delete($fullPath, $true)
}

function New-InstallSiblingDirectory([string]$Path, [string]$Kind) {
    [void][IO.Directory]::CreateDirectory($Path)
    # Keep the Program Files inherited DACL, but make the owner an explicit
    # system-controlled principal before accepting it as an activation leaf.
    $acl = [IO.Directory]::GetAccessControl($Path, [System.Security.AccessControl.AccessControlSections]::Owner -bor [System.Security.AccessControl.AccessControlSections]::Access)
    $acl.SetOwner($script:AdministratorsSid)
    [IO.Directory]::SetAccessControl($Path, $acl)
    Assert-InstallSibling $Path $Kind | Out-Null
}

function Assert-InstalledPayload([string]$Root) {
    $leaf = [IO.Path]::GetFileName((Get-FullPath $Root))
    if ($leaf -match ("^" + [regex]::Escape($script:InstallSiblingPrefix) + '-(Stage|Backup)-[a-f0-9]{32}$')) {
        $root = Assert-InstallSibling $Root $Matches[1]
    } else {
        $root = Assert-SafeProgramFilesDestination $Root
    }
    foreach ($relativePath in @("VRCForge.exe", "VRCForge.png", "backend\\vrcforge_backend.exe", "VERSION", "payload-integrity.json")) {
        $path = Assert-ContainedPath $root (Join-Path $root $relativePath)
        Assert-NoReparsePath $path | Out-Null
        if (-not [IO.File]::Exists($path)) { Fail "Extracted payload is missing $relativePath." }
    }
    $manifestPath = Join-Path $root "payload-integrity.json"
    try { $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json } catch { Fail "Extracted payload integrity manifest is invalid." }
    if ($manifest.schema -ne "vrcforge.payload-integrity.v1" -or $manifest.version -ne $Version) { Fail "Extracted payload integrity manifest is not version-bound." }
    foreach ($entryName in @("desktop", "backend", "version", "notificationIcon")) {
        $entry = $manifest.files.$entryName
        if ($null -eq $entry -or [string]::IsNullOrWhiteSpace([string]$entry.relativePath) -or [string]$entry.sha256 -notmatch "^[0-9a-fA-F]{64}$") { Fail "Extracted payload integrity entry is invalid: $entryName" }
        $path = Assert-ContainedPath $root (Join-Path $root ([string]$entry.relativePath))
        Assert-NoReparsePath $path | Out-Null
        $stream = New-Object IO.FileStream($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
        try { $digest = Get-Sha256 $stream } finally { $stream.Dispose() }
        if ($digest -ne ([string]$entry.sha256).ToLowerInvariant()) { Fail "Extracted payload integrity digest mismatch: $entryName" }
    }
}

function Assert-InstallNotRunning([string]$Root) {
    $root = Assert-SafeProgramFilesDestination $Root
    if (-not [IO.Directory]::Exists($root)) { return }
    Assert-NoReparseTree $root
    foreach ($relativePath in @("VRCForge.exe", "backend\\vrcforge_backend.exe")) {
        $path = Assert-ContainedPath $root (Join-Path $root $relativePath)
        if (-not [IO.File]::Exists($path)) { continue }
        Assert-NoReparsePath $path | Out-Null
        try {
            $stream = New-Object IO.FileStream($path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
            $stream.Dispose()
        } catch {
            Fail "The installed $relativePath is still in use. Close VRCForge before continuing."
        }
    }
}

function New-PrivateStageDirectory([string]$Path) {
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($sid in @($script:SystemSid, $script:AdministratorsSid)) {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($sid, [System.Security.AccessControl.FileSystemRights]::FullControl, $inheritance, [System.Security.AccessControl.PropagationFlags]::None, [System.Security.AccessControl.AccessControlType]::Allow)
        [void]$acl.AddAccessRule($rule)
    }
    # The elevated installer token normally has Administrators as an assignable
    # owner.  Refuse to continue if Windows cannot establish that boundary.
    $acl.SetOwner($script:AdministratorsSid)
    [void][IO.Directory]::CreateDirectory($Path, $acl)
}

function Assert-PrivateStageAcl([string]$Path) {
    $acl = [IO.Directory]::GetAccessControl($Path, [System.Security.AccessControl.AccessControlSections]::Owner -bor [System.Security.AccessControl.AccessControlSections]::Access)
    if (-not $acl.AreAccessRulesProtected) { Fail "The staging DACL is inherited." }
    $ownerSid = $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
    if ($ownerSid -notin @($script:SystemSid.Value, $script:AdministratorsSid.Value)) {
        Fail "The staging owner is not a system-controlled principal."
    }
    $rules = @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) { Fail "The staging DACL does not have exactly two explicit rules." }
    $seen = @{}
    foreach ($rule in $rules) {
        if ($rule.IdentityReference.Value -notin @($script:SystemSid.Value, $script:AdministratorsSid.Value) -or
            $rule.AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
            $rule.FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl -or
            $rule.InheritanceFlags -ne ([System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit) -or
            $rule.PropagationFlags -ne [System.Security.AccessControl.PropagationFlags]::None -or $seen.ContainsKey($rule.IdentityReference.Value)) {
            Fail "The staging DACL grants an unexpected principal or right."
        }
        $seen[$rule.IdentityReference.Value] = $true
    }
    if (-not $seen.ContainsKey($script:SystemSid.Value) -or -not $seen.ContainsKey($script:AdministratorsSid.Value)) { Fail "The staging DACL is missing a required principal." }
}

function New-StageRoot {
    $programFiles = Get-TrustedProgramFilesRoot
    $parent = Join-Path $programFiles $script:StageParentName
    if (-not [IO.Directory]::Exists($parent)) { New-PrivateStageDirectory $parent }
    Assert-NoReparsePath $parent | Out-Null
    Assert-PrivateStageAcl $parent
    for ($attempt = 0; $attempt -lt 8; $attempt++) {
        $nonce = [Guid]::NewGuid().ToString("N")
        $root = Join-Path $parent $nonce
        try {
            New-PrivateStageDirectory $root
            Assert-NoReparsePath $root | Out-Null
            Assert-PrivateStageAcl $root
            return $root
        } catch {
            if ([IO.Directory]::Exists($root)) { Remove-ValidatedStageRoot $root }
            if ($attempt -eq 7) { throw }
        }
    }
    Fail "Could not allocate a unique staging nonce."
}

function Assert-ValidatedStageRoot([string]$Path) {
    $fullPath = Assert-NoReparsePath $Path
    $parent = [IO.Directory]::GetParent($fullPath)
    $expectedParent = Join-Path (Get-TrustedProgramFilesRoot) $script:StageParentName
    if ($null -eq $parent -or $parent.FullName -cne (Get-FullPath $expectedParent) -or ([IO.Path]::GetFileName($fullPath) -notmatch '^[a-f0-9]{32}$')) {
        Fail "StageRoot is not a verified nonce leaf."
    }
    Assert-NoReparsePath $expectedParent | Out-Null
    Assert-PrivateStageAcl $expectedParent
    Assert-PrivateStageAcl $fullPath
    return $fullPath
}

function Remove-ValidatedStageRoot([string]$Path) {
    try {
        $fullPath = Assert-ValidatedStageRoot $Path
        [IO.Directory]::Delete($fullPath, $true)
    } catch {
        # Cleanup must never broaden to a parent or follow a replacement link.
    }
}

function Get-Sha256([IO.Stream]$Stream) {
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($Stream))).Replace("-", "").ToLowerInvariant() }
    finally { $hasher.Dispose() }
}

function Get-ValidatedEntryParts([string]$Name) {
    # Windows Compress-Archive writes ZIP member separators as backslashes.
    # Project every accepted archive into one forward-slash logical namespace
    # before any traversal, collision, or device-name decision.
    $logicalName = $Name.Replace('\', '/')
    if ([string]::IsNullOrWhiteSpace($logicalName) -or $logicalName.StartsWith('/') -or $logicalName.Contains(':')) {
        Fail "Archive entry has an absolute, drive, or ADS-like name."
    }
    $parts = $logicalName.Split('/')
    foreach ($part in $parts) {
        if ([string]::IsNullOrEmpty($part) -or $part -eq '.' -or $part -eq '..' -or $part.EndsWith('.') -or $part.EndsWith(' ') -or $part.IndexOfAny([char[]]'<>:"|?*') -ge 0) {
            Fail "Archive entry has an unsafe path segment."
        }
        $baseName = $part.Split('.')[0].TrimEnd(' ').ToUpperInvariant()
        if ($baseName -in @('CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9')) {
            Fail "Archive entry uses a reserved device name."
        }
    }
    return $parts
}

function Assert-RegularZipEntry($Entry) {
    # The high Unix mode bits identify symlinks even when a ZIP was authored on
    # a non-Windows host. Directories are represented by their trailing slash.
    $unixType = (($Entry.ExternalAttributes -shr 16) -band 0xF000)
    $logicalName = $Entry.FullName.Replace('\', '/')
    if ($unixType -eq 0xA000 -or ($unixType -ne 0 -and $unixType -ne 0x8000 -and -not $logicalName.EndsWith('/'))) {
        Fail "Archive contains a non-regular entry."
    }
}

function Test-ZipLayout([IO.Compression.ZipArchive]$Archive) {
    if ($Archive.Entries.Count -gt $script:MaxEntryCount) { Fail "Archive has too many entries." }
    [UInt64]$total = 0
    $types = @{}
    $terminals = @{}
    foreach ($entry in $Archive.Entries) {
        Assert-RegularZipEntry $entry
        $logicalName = $entry.FullName.Replace('\', '/')
        $isDirectory = $logicalName.EndsWith('/')
        $name = if ($isDirectory) { $logicalName.Substring(0, $logicalName.Length - 1) } else { $logicalName }
        $parts = @(Get-ValidatedEntryParts $name)
        $terminalKey = (($parts | ForEach-Object { $_.Normalize([Text.NormalizationForm]::FormC).ToUpperInvariant() }) -join '/')
        if ($terminals.ContainsKey($terminalKey)) { Fail "Archive has a duplicate normalized entry name." }
        $terminals[$terminalKey] = $true
        if (-not $isDirectory) {
            if ([UInt64]$entry.Length -gt $script:MaxEntryBytes) { Fail "Archive entry exceeds the per-file limit." }
            $total += [UInt64]$entry.Length
            if ($total -gt $script:MaxExtractedBytes) { Fail "Archive exceeds the total extraction limit." }
        }
        for ($i = 0; $i -lt $parts.Length; $i++) {
            $key = (($parts[0..$i] | ForEach-Object { $_.Normalize([Text.NormalizationForm]::FormC).ToUpperInvariant() }) -join '/')
            $wanted = if ($i -eq $parts.Length - 1 -and -not $isDirectory) { 'file' } else { 'directory' }
            if ($types.ContainsKey($key) -and $types[$key] -ne $wanted) { Fail "Archive has a file/directory collision." }
            $types[$key] = $wanted
        }
    }
}

function Open-VerifiedArchive([string]$PayloadPath) {
    Assert-NoReparsePath $PayloadPath | Out-Null
    $stream = New-Object IO.FileStream($PayloadPath, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
    try {
        if ([UInt64]$stream.Length -ne $ExpectedLength) { Fail "Payload length does not match the release manifest." }
        $hash = Get-Sha256 $stream
        if (-not [string]::Equals($hash, $ExpectedSha256.ToLowerInvariant(), [StringComparison]::Ordinal)) { Fail "Payload SHA-256 does not match the release manifest." }
        $stream.Position = 0
        $archive = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Read, $true)
        try { Test-ZipLayout $archive } finally { $archive.Dispose() }
        $stream.Position = 0
        return $stream
    } catch { $stream.Dispose(); throw }
}

function Write-State([string]$Path, [string]$Root) {
    $fullPath = Get-FullPath $Path
    $parent = [IO.Path]::GetDirectoryName($fullPath)
    if (-not [IO.Directory]::Exists($parent)) { Fail "StatePath parent does not exist." }
    Assert-NoReparsePath $parent | Out-Null
    if ([IO.File]::Exists($fullPath)) { Fail "StatePath already exists." }
    # NSIS reads this as an opaque canonical path. Keep it BOM-free and without
    # a newline so no parser or shell interpretation is involved.
    $bytes = [Text.Encoding]::UTF8.GetBytes((Get-FullPath $Root))
    $stream = New-Object IO.FileStream($fullPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}

function Get-ReleaseResponse {
    $currentUri = [Uri]::new($PayloadUrl, [UriKind]::Absolute)
    for ($redirectCount = 0; $redirectCount -le 5; $redirectCount++) {
        if ($currentUri.Scheme -cne "https" -or -not [string]::IsNullOrEmpty($currentUri.UserInfo) -or -not [string]::IsNullOrEmpty($currentUri.Fragment)) {
            Fail "The release redirect is not a trusted HTTPS URI."
        }
        if ($redirectCount -eq 0) {
            if (-not [string]::Equals($currentUri.AbsoluteUri, $PayloadUrl, [StringComparison]::Ordinal)) {
                Fail "The initial release URI changed before download."
            }
        } elseif ($currentUri.Host -cne "release-assets.githubusercontent.com") {
            Fail "The release redirect host is not trusted."
        }

        $request = [Net.HttpWebRequest]::Create($currentUri)
        $request.Method = "GET"
        $request.AllowAutoRedirect = $false
        $request.Timeout = 120000
        $request.ReadWriteTimeout = 120000
        $request.UserAgent = "VRCForge-Web-Installer/$Version"
        $response = $request.GetResponse()
        if ($response.StatusCode -eq [Net.HttpStatusCode]::OK) {
            return $response
        }
        if ([int]$response.StatusCode -notin @(301, 302, 303, 307, 308)) {
            $response.Close()
            Fail "The release server returned an unexpected HTTP status."
        }
        $location = [string]$response.Headers["Location"]
        $response.Close()
        if ([string]::IsNullOrWhiteSpace($location)) { Fail "The release redirect omitted its destination." }
        $currentUri = [Uri]::new($currentUri, $location)
    }
    Fail "The release download exceeded its redirect limit."
}

function Invoke-Prepare {
    if ([string]::IsNullOrWhiteSpace($StatePath)) { Fail "Prepare requires StatePath." }
    $root = New-StageRoot
    try {
        $payloadPath = Join-Path $root $script:PayloadFileName
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $response = Get-ReleaseResponse
        try {
            if ($response.StatusCode -ne [Net.HttpStatusCode]::OK -or ($response.ContentLength -ge 0 -and [UInt64]$response.ContentLength -ne $ExpectedLength)) { Fail "The release server returned an unexpected payload response." }
            $input = $response.GetResponseStream()
            $output = New-Object IO.FileStream($payloadPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $buffer = New-Object byte[] 65536
                [UInt64]$written = 0
                while (($read = $input.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $written += [UInt64]$read
                    if ($written -gt $ExpectedLength -or $written -gt $script:MaxArchiveBytes) { Fail "The downloaded payload exceeds its permitted length." }
                    $output.Write($buffer, 0, $read)
                }
                $output.Flush($true)
            } finally { $output.Dispose(); $input.Dispose() }
        } finally { $response.Close() }
        $verified = Open-VerifiedArchive $payloadPath
        $verified.Dispose()
        Write-State $StatePath $root
        [pscustomobject]@{ schema = $script:StateSchema; stageRoot = $root; payloadPath = $payloadPath; version = $Version } | ConvertTo-Json -Compress
    } catch { Remove-ValidatedStageRoot $root; throw }
}

function Invoke-ValidateDestination {
    if ([string]::IsNullOrWhiteSpace($DestinationRoot)) { Fail "ValidateDestination requires DestinationRoot." }
    $destination = Assert-SafeProgramFilesDestination $DestinationRoot
    Assert-InstallNotRunning $destination
    [pscustomobject]@{ schema = $script:StateSchema; validatedDestination = $destination; version = $Version } | ConvertTo-Json -Compress
}

function Invoke-Extract {
    if ([string]::IsNullOrWhiteSpace($DestinationRoot)) { Fail "Extract requires DestinationRoot." }
    if ([string]::IsNullOrWhiteSpace($PayloadPath) -and [string]::IsNullOrWhiteSpace($StageRoot)) { Fail "Extract requires a verified StageRoot or embedded PayloadPath." }
    $downloadRoot = $null
    if (-not [string]::IsNullOrWhiteSpace($PayloadPath)) {
        # The offline installer's embedded ZIP arrives through PluginDir, which
        # is deliberately treated as an untrusted transport only.  The exact
        # bytes are held FileShare.None through digest, layout, and extraction.
        $payloadPath = Assert-NoReparsePath $PayloadPath
    } else {
        $downloadRoot = Assert-ValidatedStageRoot $StageRoot
        $payloadPath = Assert-ContainedPath $downloadRoot (Join-Path $downloadRoot $script:PayloadFileName)
    }
    $stream = Open-VerifiedArchive $payloadPath
    $installStage = $null
    $backup = $null
    $oldMoved = $false
    $newMoved = $false
    $activationCommitted = $false
    try {
        # Do not touch an existing installation until held archive bytes have
        # passed length, digest, and layout checks and a complete new payload is
        # extracted in a same-volume Program Files sibling.
        $destination = Assert-SafeProgramFilesDestination $DestinationRoot
        Assert-InstallNotRunning $destination
        $installStage = Get-InstallSiblingPath "Stage"
        New-InstallSiblingDirectory $installStage "Stage"
        $archive = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Read, $true)
        try {
            Test-ZipLayout $archive
            foreach ($entry in $archive.Entries) {
                $logicalName = $entry.FullName.Replace('\', '/')
                $isDirectory = $logicalName.EndsWith('/')
                $name = if ($isDirectory) { $logicalName.Substring(0, $logicalName.Length - 1) } else { $logicalName }
                $parts = @(Get-ValidatedEntryParts $name)
                $target = $installStage
                foreach ($part in $parts) { $target = Join-Path $target $part }
                $target = Assert-ContainedPath $installStage $target
                if ($isDirectory) {
                    if (-not [IO.Directory]::Exists($target)) { [void][IO.Directory]::CreateDirectory($target) }
                    Assert-NoReparsePath $target | Out-Null
                    continue
                }
                $targetParent = [IO.Path]::GetDirectoryName($target)
                if (-not [IO.Directory]::Exists($targetParent)) { [void][IO.Directory]::CreateDirectory($targetParent) }
                Assert-NoReparsePath $targetParent | Out-Null
                if ([IO.File]::Exists($target)) { Fail "Refusing to overwrite an existing extraction target." }
                $entryStream = $entry.Open()
                $output = New-Object IO.FileStream($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                try { $entryStream.CopyTo($output); $output.Flush($true) } finally { $output.Dispose(); $entryStream.Dispose() }
            }
        } finally { $archive.Dispose() }
        Assert-InstalledPayload $installStage

        if ([IO.Directory]::Exists($destination)) {
            $backup = Get-InstallSiblingPath "Backup"
            Assert-SafeProgramFilesDestination $destination | Out-Null
            [IO.Directory]::Move($destination, $backup)
            $oldMoved = $true
            Assert-InstallSibling $backup "Backup" | Out-Null
        }
        [IO.Directory]::Move($installStage, $destination)
        $newMoved = $true
        $installStage = $null
        Assert-InstalledPayload $destination
        $activationCommitted = $true
        if ($null -ne $backup) {
            try { Remove-InstallSibling $backup "Backup" }
            catch { Write-Warning "The verified prior-install backup could not be removed and was left at its exact sibling path." }
            $backup = $null
        }
    } catch {
        $failure = $_
        # A failed activation must restore the pre-existing install; cleanup
        # never broadens past the exact checked Program Files leaf.
        if (-not $activationCommitted -and $oldMoved -and $null -ne $backup) {
            try {
                if ($newMoved -and [IO.Directory]::Exists($DestinationRoot)) {
                    $current = Assert-SafeProgramFilesDestination $DestinationRoot
                    Assert-NoReparseTree $current
                    [IO.Directory]::Delete($current, $true)
                }
                if ([IO.Directory]::Exists($backup)) {
                    Assert-InstallSibling $backup "Backup" | Out-Null
                    [IO.Directory]::Move($backup, (Assert-SafeProgramFilesDestination $DestinationRoot))
                    $backup = $null
                }
            } catch {
                Fail "Activation failed and the prior installation could not be restored: $($_.Exception.Message)"
            }
        }
        throw $failure
    } finally {
        $stream.Dispose()
        if ($null -ne $installStage -and [IO.Directory]::Exists($installStage)) {
            try { Remove-InstallSibling $installStage "Stage" } catch { }
        }
        if ($null -ne $downloadRoot) { Remove-ValidatedStageRoot $downloadRoot }
    }
    [pscustomobject]@{ schema = $script:StateSchema; extractedTo = $destination; version = $Version } | ConvertTo-Json -Compress
}

Assert-Inputs
Add-Type -AssemblyName System.IO.Compression
if ($Action -eq "ValidateDestination") { Invoke-ValidateDestination } elseif ($Action -eq "Prepare") { Invoke-Prepare } else { Invoke-Extract }
