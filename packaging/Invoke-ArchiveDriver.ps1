param(
    [Parameter(Mandatory)][string]$RequestPath,
    [string]$CandidatePath
)
$ErrorActionPreference = 'Stop'
if (-not $CandidatePath) {
    $CandidatePath = Join-Path $PSScriptRoot 'PassiveListener-0.1.0.exe'
    if (-not (Test-Path -LiteralPath $CandidatePath -PathType Leaf)) {
        $CandidatePath = Join-Path $PSScriptRoot '../dist/PassiveListener-0.1.0.exe'
    }
}
if (-not (Test-Path -LiteralPath $CandidatePath -PathType Leaf)) {
    throw 'Build the candidate before running the archive adapter'
}
& $CandidatePath archive-test-request $RequestPath
if ($LASTEXITCODE -ne 0) { throw 'Production archive adapter failed' }
