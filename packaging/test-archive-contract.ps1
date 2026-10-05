$ErrorActionPreference = 'Stop'
# Read the immutable QA-owned contract without changing QA files or its branch.
$qaCommit = '5cf728e55457175d0f37a1c3d16f0264ab9870f7'
git fetch --no-tags origin $qaCommit
if ($LASTEXITCODE -ne 0) { throw 'Pinned QA contract fetch failed' }
New-Item -ItemType Directory -Force build/qa | Out-Null
git archive --format=zip --output=build/qa-contract.zip $qaCommit tests/acceptance benchmarks
if ($LASTEXITCODE -ne 0) { throw 'QA contract extraction failed' }
Expand-Archive -LiteralPath build/qa-contract.zip -DestinationPath build/qa -Force
Push-Location build/qa
try {
    python -m unittest discover -s tests/acceptance -v
    if ($LASTEXITCODE -ne 0) { throw 'Pinned QA harness tests failed' }
} finally { Pop-Location }
python build/qa/tests/acceptance/archive_contract.py --driver packaging/Invoke-ArchiveDriver.ps1 --evidence build/archive-evidence
if ($LASTEXITCODE -ne 0) { throw 'Production EXE failed QA archive oracle' }
Get-Content build/archive-evidence/archive.json
