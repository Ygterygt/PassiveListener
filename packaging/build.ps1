$ErrorActionPreference = 'Stop'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $PWD 'build/pyinstaller-cache'
python -m PyInstaller --clean --noconfirm --onefile --paths src --name PassiveListener-0.1.0 packaging/entrypoint.py
if ($LASTEXITCODE -ne 0) { throw 'EXE build failed' }
$exe = Join-Path $PWD 'dist/PassiveListener-0.1.0.exe'
& $exe --version
if ($LASTEXITCODE -ne 0) { throw 'EXE startup failed' }
$fixture = Join-Path $PWD 'build/synthetic.bin'
[IO.File]::WriteAllBytes($fixture, [Text.Encoding]::UTF8.GetBytes('synthetic fixture only'))
$hash = (Get-FileHash -LiteralPath $fixture -Algorithm SHA256).Hash.ToLowerInvariant()
& $exe verify-artifact $fixture --sha256 $hash --size 22
if ($LASTEXITCODE -ne 0) { throw 'EXE valid artifact test failed' }
& $exe verify-artifact $fixture --sha256 ('0' * 64) --size 22
if ($LASTEXITCODE -ne 2) { throw 'EXE corruption test failed' }
$modelFolder = Join-Path $PWD 'build/synthetic-models'
New-Item -ItemType Directory -Force -Path $modelFolder | Out-Null
Copy-Item -LiteralPath $fixture -Destination (Join-Path $modelFolder 'ggml-base.bin')
& $exe verify-model whisper-base $modelFolder
if ($LASTEXITCODE -ne 2) { throw 'EXE compiled model pin rejection failed' }
$checksum = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant()
"$checksum  PassiveListener-0.1.0.exe" | Set-Content -Encoding ascii 'dist/SHA256SUMS.txt'
Copy-Item LICENSE dist/LICENSE.txt
python packaging/collect_notices.py
if ($LASTEXITCODE -ne 0) { throw 'Notice collection failed' }

'UNSIGNED engineering foundation: artifact validation only. No capture, service or installer yet. Not approved for release.' | Set-Content 'dist/UNSIGNED.txt'
