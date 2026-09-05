$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Repo = 'slavagostev2-dot/CS2-Value'
$ChannelUrl = "https://raw.githubusercontent.com/$Repo/main/latest.json"
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VersionPath = Join-Path $Root 'VERSION.txt'
$DataDir = Join-Path $Root 'data'
$DbPath = Join-Path $DataDir 'cs2_value.db'
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$CliExe = Join-Path $Root '.venv\Scripts\cs2-value.exe'

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

function Fail([string]$Text) {
    Write-Host ""
    Write-Host $Text -ForegroundColor Red
    exit 1
}

function Assert-LastExit([string]$What) {
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed with exit code $LASTEXITCODE"
    }
}

function Parse-Version([string]$Text) {
    $m = [regex]::Match($Text.Trim(), '^(?:v)?(\d+)\.(\d+)\.(\d+)$')
    if (-not $m.Success) { throw "Unsupported version format: $Text" }
    return [Version]::new([int]$m.Groups[1].Value, [int]$m.Groups[2].Value, [int]$m.Groups[3].Value)
}

function Assert-SafeRelativePath([string]$PathText) {
    if ([string]::IsNullOrWhiteSpace($PathText)) { throw 'Update manifest contains an empty path.' }
    if ([IO.Path]::IsPathRooted($PathText)) { throw "Unsafe absolute update path: $PathText" }
    $normalized = $PathText.Replace('/', '\')
    foreach ($part in $normalized.Split('\')) {
        if ($part -eq '..') { throw "Unsafe parent path in update manifest: $PathText" }
    }
    return $normalized
}

function Get-Json([string]$Url) {
    try {
        return Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 15 -Headers @{ 'User-Agent' = 'CS2-Value-Updater' }
    } catch {
        throw "Could not read update metadata from GitHub: $($_.Exception.Message)"
    }
}

function Assert-Sha256([string]$Path, [string]$Expected, [string]$Label) {
    $want = $Expected.ToLowerInvariant()
    if ($want -notmatch '^[0-9a-f]{64}$') { throw "Invalid SHA-256 for $Label" }
    if (-not (Test-Path $Path -PathType Leaf)) { throw "Required file not found for patch: $Label" }
    $actual = (Get-FileHash -Algorithm SHA256 $Path).Hash.ToLowerInvariant()
    if ($actual -ne $want) { throw "SHA-256 mismatch before/after patch for $Label. Expected $want, got $actual." }
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

if (-not (Test-Path $VersionPath)) { Fail "VERSION.txt not found: $VersionPath" }
if (-not (Test-Path $VenvPython)) { Fail 'Python environment not found. Run INSTALL.bat first.' }

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$CurrentText = (Get-Content -Raw -Encoding UTF8 $VersionPath).Trim()
$Current = Parse-Version $CurrentText

Write-Host "CS2 Value - GitHub Update" -ForegroundColor Green
Write-Host "Installed: v$CurrentText"
Write-Host "Channel:   $ChannelUrl"

Write-Step 'Checking GitHub for updates'
$Channel = Get-Json $ChannelUrl
if ([int]$Channel.schema -ne 1) { throw "Unsupported update channel schema: $($Channel.schema)" }
$LatestText = [string]$Channel.latest_version
$Latest = Parse-Version $LatestText
Write-Host "Latest:    v$LatestText"

if ($Current -ge $Latest) {
    Write-Host ""
    Write-Host 'You already have the latest version.' -ForegroundColor Green
    exit 0
}

$Releases = @($Channel.releases)
$Plan = @()
$CursorText = $CurrentText
$Guard = 0
while ((Parse-Version $CursorText) -lt $Latest) {
    $Guard++
    if ($Guard -gt 50) { throw 'Update chain is too long or cyclic.' }
    $Next = $Releases | Where-Object { ([string]$_.from).TrimStart('v') -eq $CursorText.TrimStart('v') } | Select-Object -First 1
    if ($null -eq $Next) {
        throw "No safe update path from v$CursorText to v$LatestText."
    }
    $Plan += $Next
    $CursorText = ([string]$Next.version).TrimStart('v')
}
if ((Parse-Version $CursorText) -ne $Latest) {
    throw "Update chain ends at v$CursorText instead of v$LatestText."
}

Write-Host "Update path: v$CurrentText -> v$LatestText" -ForegroundColor Yellow
foreach ($p in $Plan) {
    Write-Host "  v$($p.from) -> v$($p.version)"
}

$TempRoot = Join-Path ([IO.Path]::GetTempPath()) ("cs2-value-update-" + [guid]::NewGuid().ToString('N'))
$StageRoot = Join-Path $TempRoot 'stage'
$BackupRoot = Join-Path $TempRoot 'backup'
New-Item -ItemType Directory -Force -Path $StageRoot, $BackupRoot | Out-Null

$Operations = @()
$UniquePaths = @{}
$BackupIndex = @{}
$DbWasBackedUp = $false

try {
    Write-Step 'Downloading and verifying update files'
    $ExpectedFrom = $CurrentText.TrimStart('v')
    foreach ($Release in $Plan) {
        $Manifest = Get-Json ([string]$Release.manifest_url)
        $ManifestFrom = ([string]$Manifest.from).TrimStart('v')
        $ManifestVersion = ([string]$Manifest.version).TrimStart('v')
        if ($ManifestFrom -ne $ExpectedFrom) {
            throw "Manifest chain mismatch: expected from v$ExpectedFrom, got v$ManifestFrom."
        }
        if ($ManifestVersion -ne ([string]$Release.version).TrimStart('v')) {
            throw "Manifest version mismatch for v$($Release.version)."
        }

        $ReleaseStage = Join-Path $StageRoot ("v" + $ManifestVersion)
        New-Item -ItemType Directory -Force -Path $ReleaseStage | Out-Null

        foreach ($File in @($Manifest.files)) {
            $Rel = Assert-SafeRelativePath ([string]$File.path)
            $ExpectedHash = ([string]$File.sha256).ToLowerInvariant()
            if ($ExpectedHash -notmatch '^[0-9a-f]{64}$') { throw "Invalid SHA-256 for $Rel" }
            $StageFile = Join-Path $ReleaseStage $Rel
            $StageDir = Split-Path -Parent $StageFile
            if ($StageDir) { New-Item -ItemType Directory -Force -Path $StageDir | Out-Null }
            Invoke-WebRequest -UseBasicParsing -Uri ([string]$File.url) -OutFile $StageFile -TimeoutSec 30 -Headers @{ 'User-Agent' = 'CS2-Value-Updater' }
            $ActualHash = (Get-FileHash -Algorithm SHA256 $StageFile).Hash.ToLowerInvariant()
            if ($ActualHash -ne $ExpectedHash) {
                throw "SHA-256 mismatch for $Rel. Expected $ExpectedHash, got $ActualHash."
            }
            $Operations += [pscustomobject]@{ kind = 'file'; path = $Rel; source = $StageFile; version = $ManifestVersion }
            $UniquePaths[$Rel.ToLowerInvariant()] = $Rel
        }

        foreach ($DeletePath in @($Manifest.delete)) {
            $Rel = Assert-SafeRelativePath ([string]$DeletePath)
            $Operations += [pscustomobject]@{ kind = 'delete'; path = $Rel; source = $null; version = $ManifestVersion }
            $UniquePaths[$Rel.ToLowerInvariant()] = $Rel
        }

        foreach ($Patch in @($Manifest.patches)) {
            $Rel = Assert-SafeRelativePath ([string]$Patch.path)
            $Before = ([string]$Patch.before_sha256).ToLowerInvariant()
            $After = ([string]$Patch.after_sha256).ToLowerInvariant()
            if ($Before -notmatch '^[0-9a-f]{64}$' -or $After -notmatch '^[0-9a-f]{64}$') {
                throw "Invalid patch SHA-256 for $Rel"
            }
            $Replacements = @($Patch.replacements)
            if ($Replacements.Count -lt 1) { throw "Patch has no replacements: $Rel" }
            $Operations += [pscustomobject]@{
                kind = 'patch'; path = $Rel; source = $null; version = $ManifestVersion;
                before = $Before; after = $After; replacements = $Replacements
            }
            $UniquePaths[$Rel.ToLowerInvariant()] = $Rel
        }
        $ExpectedFrom = $ManifestVersion
    }

    Write-Step 'Creating rollback backup'
    foreach ($Rel in $UniquePaths.Values) {
        $Target = Join-Path $Root $Rel
        $BackupFile = Join-Path $BackupRoot $Rel
        if (Test-Path $Target -PathType Leaf) {
            $BackupDir = Split-Path -Parent $BackupFile
            if ($BackupDir) { New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null }
            Copy-Item -Force $Target $BackupFile
            $BackupIndex[$Rel.ToLowerInvariant()] = $true
        } else {
            $BackupIndex[$Rel.ToLowerInvariant()] = $false
        }
    }
    if (Test-Path $DbPath -PathType Leaf) {
        New-Item -ItemType Directory -Force -Path (Join-Path $BackupRoot 'data') | Out-Null
        Copy-Item -Force $DbPath (Join-Path $BackupRoot 'data\cs2_value.db')
        $DbWasBackedUp = $true
    }

    Write-Step 'Applying update'
    foreach ($Op in $Operations) {
        $Target = Join-Path $Root $Op.path
        if ($Op.kind -eq 'file') {
            $TargetDir = Split-Path -Parent $Target
            if ($TargetDir) { New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null }
            Copy-Item -Force $Op.source $Target
        } elseif ($Op.kind -eq 'patch') {
            Assert-Sha256 $Target $Op.before $Op.path
            $Text = [IO.File]::ReadAllText($Target, [Text.UTF8Encoding]::new($false))
            foreach ($Replacement in @($Op.replacements)) {
                $Old = [string]$Replacement.old
                $New = [string]$Replacement.new
                $ExpectedCount = 1
                if ($null -ne $Replacement.count) { $ExpectedCount = [int]$Replacement.count }
                if ($ExpectedCount -lt 1) { throw "Invalid replacement count for $($Op.path)" }
                $ActualCount = ([regex]::Matches($Text, [regex]::Escape($Old))).Count
                if ($ActualCount -ne $ExpectedCount) {
                    throw "Patch anchor count mismatch for $($Op.path): expected $ExpectedCount, found $ActualCount."
                }
                $Text = $Text.Replace($Old, $New)
            }
            Write-Utf8NoBom $Target $Text
            Assert-Sha256 $Target $Op.after $Op.path
        } else {
            if (Test-Path $Target -PathType Leaf) { Remove-Item -Force $Target }
        }
    }

    $NewVersion = (Get-Content -Raw -Encoding UTF8 $VersionPath).Trim()
    if ((Parse-Version $NewVersion) -ne $Latest) {
        throw "Updated VERSION.txt says v$NewVersion, expected v$LatestText."
    }

    Push-Location $Root
    try {
        Write-Step 'Installing/updating dependencies'
        & $VenvPython -m pip install -e '.[dev,model,browser]'
        Assert-LastExit 'Dependency installation'

        Write-Step 'Updating database schema'
        & $CliExe init-db
        Assert-LastExit 'Database initialization'

        Write-Step 'Running automatic tests'
        & $VenvPython -m pytest -q
        Assert-LastExit 'Tests'
    } finally {
        Pop-Location
    }

    Write-Host ""
    Write-Host "SUCCESS: updated to v$LatestText" -ForegroundColor Green
    Write-Host 'Database, API keys and logs were preserved.' -ForegroundColor Green
    exit 10
}
catch {
    Write-Host ""
    Write-Host "UPDATE FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'Restoring previous program files...' -ForegroundColor Yellow
    try {
        foreach ($Rel in $UniquePaths.Values) {
            $Target = Join-Path $Root $Rel
            $Key = $Rel.ToLowerInvariant()
            if ($BackupIndex.ContainsKey($Key) -and $BackupIndex[$Key]) {
                $BackupFile = Join-Path $BackupRoot $Rel
                $TargetDir = Split-Path -Parent $Target
                if ($TargetDir) { New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null }
                Copy-Item -Force $BackupFile $Target
            } else {
                if (Test-Path $Target -PathType Leaf) { Remove-Item -Force $Target }
            }
        }
        if ($DbWasBackedUp) {
            Copy-Item -Force (Join-Path $BackupRoot 'data\cs2_value.db') $DbPath
        }
        Push-Location $Root
        try {
            & $VenvPython -m pip install -e '.[dev,model,browser]' | Out-Null
        } finally {
            Pop-Location
        }
        Write-Host 'Rollback completed.' -ForegroundColor Green
    } catch {
        Write-Host "Rollback also had an error: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "Backup folder was: $BackupRoot" -ForegroundColor Yellow
    }
    exit 1
}
finally {
    if (Test-Path $TempRoot) {
        Remove-Item -Recurse -Force $TempRoot -ErrorAction SilentlyContinue
    }
}
