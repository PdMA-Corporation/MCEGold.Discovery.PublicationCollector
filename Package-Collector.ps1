param(
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

$ExpectedSeedHash = "0D86C8F6181411FF82437084156535C2AB85542D1D16981E9012BA0855922EE9"
$PackageName = "MCEGold.Discovery.PublicationCollector-v$Version"

function Get-RepoRoot {
    $scriptPath = Split-Path -Parent $PSCommandPath
    return (Resolve-Path $scriptPath).Path
}

function Assert-FileExists {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file is missing: $Path"
    }
}

function Assert-DirectoryExists {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Required directory is missing: $Path"
    }
}

function Copy-RequiredFile {
    param(
        [string]$Source,
        [string]$DestinationRoot,
        [string]$RelativePath
    )
    $destination = Join-Path $DestinationRoot $RelativePath
    $destinationDirectory = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $destinationDirectory | Out-Null
    Copy-Item -LiteralPath $Source -Destination $destination -Force
}

function Copy-RequiredTree {
    param(
        [string]$SourceRoot,
        [string]$DestinationRoot,
        [string]$RelativeRoot
    )
    Assert-DirectoryExists $SourceRoot
    Get-ChildItem -LiteralPath $SourceRoot -Recurse -File | ForEach-Object {
        if ($_.FullName -match "\\__pycache__\\") {
            return
        }
        if ($_.Name -like "*.pyc") {
            return
        }
        $relativeChild = [System.IO.Path]::GetRelativePath($SourceRoot, $_.FullName)
        $relativePackagePath = Join-Path $RelativeRoot $relativeChild
        Copy-RequiredFile -Source $_.FullName -DestinationRoot $DestinationRoot -RelativePath $relativePackagePath
    }
}

$repoRoot = Get-RepoRoot
$artifactsDir = Join-Path $repoRoot "artifacts"
$archivePath = Join-Path $artifactsDir "$PackageName.tar.gz"
$stagingParent = Join-Path ([System.IO.Path]::GetTempPath()) ([System.Guid]::NewGuid().ToString("N"))
$packageRoot = Join-Path $stagingParent $PackageName

try {
    $pyprojectPath = Join-Path $repoRoot "pyproject.toml"
    $initPath = Join-Path $repoRoot "src\mcegold_discovery_publication_collector\__init__.py"
    $seedPath = Join-Path $repoRoot "data\discovery.seed.db"

    Assert-FileExists $pyprojectPath
    Assert-FileExists $initPath
    Assert-FileExists $seedPath

    $pyprojectText = Get-Content -LiteralPath $pyprojectPath -Raw
    if ($pyprojectText -notmatch "(?m)^version\s*=\s*`"$([regex]::Escape($Version))`"$") {
        throw "pyproject.toml version is not $Version."
    }

    $initText = Get-Content -LiteralPath $initPath -Raw
    if ($initText -notmatch "__version__\s*=\s*`"$([regex]::Escape($Version))`"") {
        throw "Runtime __version__ is not $Version."
    }

    $actualSeedHash = (Get-FileHash -LiteralPath $seedPath -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actualSeedHash -ne $ExpectedSeedHash) {
        throw "Seed database SHA-256 mismatch. Expected $ExpectedSeedHash but found $actualSeedHash."
    }

    New-Item -ItemType Directory -Force -Path $artifactsDir | Out-Null
    if (Test-Path -LiteralPath $archivePath) {
        Remove-Item -LiteralPath $archivePath -Force
    }
    New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

    $requiredFiles = @(
        "README.md",
        "LICENSE",
        "pyproject.toml",
        "config\collector.example.json",
        "data\discovery.seed.db",
        "docs\deployment.md",
        "docs\discovery.md"
    )

    foreach ($relativePath in $requiredFiles) {
        $source = Join-Path $repoRoot $relativePath
        Assert-FileExists $source
        Copy-RequiredFile -Source $source -DestinationRoot $packageRoot -RelativePath $relativePath
    }

    Copy-RequiredTree `
        -SourceRoot (Join-Path $repoRoot "src\mcegold_discovery_publication_collector") `
        -DestinationRoot $packageRoot `
        -RelativeRoot "src\mcegold_discovery_publication_collector"

    $python = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $python = "python"
    }
    $packCommand = @"
import pathlib
import tarfile

archive_path = pathlib.Path(r"$archivePath")
staging_parent = pathlib.Path(r"$stagingParent")
package_name = "$PackageName"

with tarfile.open(archive_path, "w:gz") as archive:
    archive.add(staging_parent / package_name, arcname=package_name)
"@
    $packCommand | & $python -
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create release archive."
    }

    $archive = Get-Item -LiteralPath $archivePath
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToUpperInvariant()
    [pscustomobject]@{
        Archive = $archive.FullName
        SizeBytes = $archive.Length
        Sha256 = $archiveHash
    }
}
finally {
    if (Test-Path -LiteralPath $stagingParent) {
        Remove-Item -LiteralPath $stagingParent -Recurse -Force
    }
}
