Param()
$ErrorActionPreference = 'Stop'

$hostName = $env:SOFILAB_HOST
$port     = $env:SOFILAB_PORT
$user     = $env:SOFILAB_USER
$keyFile  = $env:SOFILAB_KEYFILE

if (-not $hostName) { throw 'SOFILAB_HOST not set' }
if (-not $port)     { throw 'SOFILAB_PORT not set' }
if (-not $user)     { throw 'SOFILAB_USER not set' }

$argsList = @()
if ($keyFile -and (Test-Path -LiteralPath $keyFile)) {
  $argsList = @('-i', $keyFile)
}

$ssh = $env:SSH_BIN
if (-not $ssh) { $ssh = 'ssh' }

& $ssh @argsList -p $port "$user@$hostName" @args
exit $LASTEXITCODE

