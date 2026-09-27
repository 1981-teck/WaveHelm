# Native parser and failure-boundary tests; the real audit matrix is a separate gate.
BeforeAll {
    $Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $Wrapper = Join-Path $Root 'tools/WaveHelm-SupplyChainMatrix.ps1'
    $HostExecutable = (Get-Process -Id $PID).Path
}

Describe 'Supply-chain matrix native wrapper' -Skip:($env:OS -ne 'Windows_NT') {
    It 'has no PowerShell parse errors' {
        $Tokens = $null
        $Errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $Wrapper, [ref] $Tokens, [ref] $Errors) | Out-Null
        @($Errors).Count | Should -Be 0
    }

    It 'rejects a missing launcher without creating output' {
        $Output = Join-Path $TestDrive 'new audit output with spaces'
        & $HostExecutable -NoProfile -NonInteractive -File $Wrapper `
            -OutputDirectory $Output -Launcher (Join-Path $TestDrive 'absent.exe') 2>&1 |
            Out-Null
        $LASTEXITCODE | Should -Be 1
        Test-Path -LiteralPath $Output | Should -BeFalse
    }

    It 'rejects a copied wrapper without its Python entrypoint' {
        $Folder = Join-Path $TestDrive 'wrapper only'
        New-Item -ItemType Directory -Path $Folder | Out-Null
        $Copy = Join-Path $Folder 'WaveHelm-SupplyChainMatrix.ps1'
        Copy-Item -LiteralPath $Wrapper -Destination $Copy
        & $HostExecutable -NoProfile -NonInteractive -File $Copy `
            -Launcher $HostExecutable 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 1
        @(Get-ChildItem -LiteralPath $Folder).Count | Should -Be 1
    }
}
