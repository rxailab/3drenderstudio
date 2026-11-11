$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
python -c "import flask,PIL" 2>$null
if ($LASTEXITCODE -ne 0) { python -m pip install -r requirements.txt }
python app.py
