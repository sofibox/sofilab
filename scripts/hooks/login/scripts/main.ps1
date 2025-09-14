param(
  [string]$Host,
  [string]$User,
  [int]$Port = 22,
  [string]$Key,
  [string]$Alias,
  [string]$SshBin
)
$ErrorActionPreference = 'Stop'

if (-not $Host) { $Host = $env:SOFILAB_HOST }
if (-not $User) { $User = $env:SOFILAB_USER }
if (-not $Port -or $Port -eq 0) { $Port = [int]($env:SOFILAB_PORT) }
if (-not $Key)  { $Key  = $env:SOFILAB_KEYFILE }
if (-not $Alias){ $Alias= $env:SOFILAB_ALIAS }
if (-not $SshBin){ $SshBin = ($env:SSH_BIN | ForEach-Object { if ($_){$_} else {'ssh'} }) }

if (-not $Host -or -not $User) {
  Write-Error 'Missing --Host/--User (or SOFILAB_HOST/SOFILAB_USER)'
  exit 2
}

$argsList = @()
if ($Key -and (Test-Path -LiteralPath $Key)) { $argsList += @('-i', $Key) }
$argsList += @('-p', [string]$Port, "$User@$Host")

& $SshBin @argsList @args
exit $LASTEXITCODE

