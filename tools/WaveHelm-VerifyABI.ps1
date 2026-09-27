[CmdletBinding()]
param(
    [string]$Python = "py",
    [string]$PythonTag = "-3.12",
    [string]$Output = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Args = @($PythonTag, "-I", "-B", (Join-Path $PSScriptRoot "verify_windows_abi.py"), "--root", $Root)
if ($Output) {
    $Args += @("--output", $Output)
}

& $Python @Args
$Exit = $LASTEXITCODE
if ($null -eq $Exit) {
    Write-Error "ABI verifier did not return an exit code."
    exit 125
}
exit $Exit
