param(
    [Parameter(Mandatory)][string]$Driver,
    [Parameter(Mandatory)][string]$EvidenceDirectory,
    [ValidateSet('install','task','archive','privacy','explicit_microphone_smoke')][string]$Suite = 'archive',
    [switch]$AllowMicrophone
)
$ErrorActionPreference = 'Stop'
if ($Suite -eq 'explicit_microphone_smoke' -and -not $AllowMicrophone) {
    throw 'Microphone smoke requires explicit -AllowMicrophone; never retain or upload personal audio.'
}
if (-not (Test-Path -LiteralPath $Driver -PathType Leaf)) { throw 'Production acceptance driver missing' }
$driverPath = (Resolve-Path -LiteralPath $Driver).Path
$cases = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'windows_cases.json') | ConvertFrom-Json
$root = [IO.Path]::GetFullPath($EvidenceDirectory)
New-Item -ItemType Directory -Path $root -Force | Out-Null
if ($Suite -eq 'archive') {
    & python (Join-Path $PSScriptRoot 'archive_contract.py') --driver $driverPath --evidence $root
    exit $LASTEXITCODE
}
if (@($cases.$Suite).Count -eq 0) { throw 'Empty acceptance suite' }
$results = @()
foreach ($case in $cases.$Suite) {
    # Host lifecycle suites still require a production driver. Archive assertions are QA-owned.
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $result = & $driverPath -Case $case -EvidenceDirectory $root
    $watch.Stop()
    if ($result -isnot [System.Collections.IDictionary] -or $result.Status -notin @('pass','fail','blocked') -or
        -not $result.Command -or -not $result.Evidence -or -not $result.Expected -or -not $result.Observed) {
        throw "Invalid evidence contract for $case"
    }
    $evidencePath = [IO.Path]::GetFullPath([IO.Path]::Combine($root, [string]$result.Evidence))
    if (-not $evidencePath.StartsWith($root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $evidencePath -PathType Leaf) -or
        (Get-Item -LiteralPath $evidencePath).Length -eq 0) {
        throw "Missing, empty or out-of-root evidence for $case"
    }
    $cursor = Get-Item -LiteralPath $evidencePath
    while ($cursor -and $cursor.FullName -ne $root) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse evidence path rejected' }
        if ($cursor -is [IO.FileInfo]) { $cursor = $cursor.Directory } else { $cursor = $cursor.Parent }
        if ($cursor.FullName -eq $root) { break }
    }
    $results += [ordered]@{ Case=$case; Status=$result.Status; Command=$result.Command;
        Expected=$result.Expected; Observed=$result.Observed; Evidence=$result.Evidence;
        DurationMs=$watch.Elapsed.TotalMilliseconds }
}
$report = [ordered]@{ Schema=1; Suite=$Suite; OS=[Environment]::OSVersion.VersionString;
    PowerShell=$PSVersionTable.PSVersion.ToString(); Results=$results }
$temp = Join-Path $root "$Suite.json.tmp"
$target = Join-Path $root "$Suite.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temp -Encoding utf8
Move-Item -LiteralPath $temp -Destination $target -Force
if (@($results | Where-Object Status -ne 'pass').Count) { exit 1 }
