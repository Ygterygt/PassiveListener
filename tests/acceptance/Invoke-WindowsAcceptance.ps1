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
$results = @()
foreach ($case in $cases.$Suite) {
    # Driver owns isolated fixture setup, actual action and assertions. Never pass transcript contents back.
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $result = & $driverPath -Case $case -EvidenceDirectory $root
    $watch.Stop()
    if ($result -isnot [System.Collections.IDictionary] -or $result.Status -notin @('pass','fail','blocked') -or
        -not $result.Command -or -not $result.Evidence -or -not $result.Expected -or -not $result.Observed) {
        throw "Invalid evidence contract for $case"
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
