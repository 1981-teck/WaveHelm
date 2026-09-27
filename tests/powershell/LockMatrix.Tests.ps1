# Native parser and failure-boundary tests. These do not run the six-target matrix.
BeforeAll {
    $Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
    $Wrapper = Join-Path $Root 'tools/WaveHelm-LockMatrix.ps1'
    $HostExecutable = (Get-Process -Id $PID).Path
}

Describe 'Lock matrix native wrapper' -Skip:($env:OS -ne 'Windows_NT') {
    It 'has no PowerShell parse errors' {
        $Tokens = $null
        $Errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $Wrapper, [ref] $Tokens, [ref] $Errors) | Out-Null
        @($Errors).Count | Should -Be 0
    }

    It 'rejects a missing launcher and does not create the requested output' {
        $Output = Join-Path $TestDrive 'new output with spaces'
        $Before = (Get-FileHash -LiteralPath $Wrapper -Algorithm SHA256).Hash
        & $HostExecutable -NoProfile -NonInteractive -File $Wrapper `
            -OutputDirectory $Output -Launcher (Join-Path $TestDrive 'absent.exe') 2>&1 |
            Out-Null
        $LASTEXITCODE | Should -Be 1
        Test-Path -LiteralPath $Output | Should -BeFalse
        (Get-FileHash -LiteralPath $Wrapper -Algorithm SHA256).Hash | Should -Be $Before
    }

    It 'rejects a wrapper copied without its Python entrypoint before launching' {
        $Folder = Join-Path $TestDrive 'wrapper only'
        New-Item -ItemType Directory -Path $Folder | Out-Null
        $Copy = Join-Path $Folder 'WaveHelm-LockMatrix.ps1'
        Copy-Item -LiteralPath $Wrapper -Destination $Copy
        & $HostExecutable -NoProfile -NonInteractive -File $Copy `
            -Launcher $HostExecutable 2>&1 | Out-Null
        $LASTEXITCODE | Should -Be 1
        @(Get-ChildItem -LiteralPath $Folder).Count | Should -Be 1
    }
}
