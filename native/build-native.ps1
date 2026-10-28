param(
    [string]$SdkPath = 'D:\MetaTrader5SDK',
    [string]$GoPath = 'go'
)
$ErrorActionPreference = 'Stop'
$nativeRoot = $PSScriptRoot
$sourceRoot = Join-Path $nativeRoot 'source'
$binRoot = Join-Path $nativeRoot 'bin'
New-Item -ItemType Directory -Path $binRoot -Force | Out-Null
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$installation = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $installation) { throw 'MSVC x64 C++ build tools are required' }
$compilerSetup = Join-Path $installation 'VC\Auxiliary\Build\vcvars64.bat'
$includeRoot = Join-Path $SdkPath 'Include'
$cppSource = Join-Path $sourceRoot 'collector.cpp'
$cppOutput = Join-Path $binRoot 'mt5-collector.exe'
$objectOutput = Join-Path $binRoot 'collector.obj'
# Run one Windows shell for the compiler's required environment setup.
$buildCommand = 'call "{0}" >nul && cl /nologo /std:c++17 /EHsc /O2 /W4 /DUNICODE /D_UNICODE /I"{1}" "{2}" /Fe:"{3}" /Fo:"{4}" /link /MACHINE:X64' -f $compilerSetup,$includeRoot,$cppSource,$cppOutput,$objectOutput
& $env:ComSpec /d /s /c $buildCommand
if ($LASTEXITCODE -ne 0) { throw 'Native MT5 collector build failed' }
Push-Location $sourceRoot
try {
    & $GoPath test ./...
    if ($LASTEXITCODE -ne 0) { throw 'Go native gateway tests failed' }
    & $GoPath build -trimpath -o (Join-Path $binRoot 'rms-gateway.exe') .
    if ($LASTEXITCODE -ne 0) { throw 'Go native gateway build failed' }
} finally { Pop-Location }
