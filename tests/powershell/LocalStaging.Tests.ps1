#Requires -Version 5.1
# Run with Pester 5 on native Windows. These are wrapper/state tests only.
# The state records below are fixtures, not successful preflight evidence.
Describe 'WaveHelm local staging delegation' {
    BeforeAll {
        $Root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
        $Script = Join-Path $Root 'tools/WaveHelm-LocalStaging.ps1'
        $Engine = (Get-Process -Id $PID).Path
        $Python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
    }

    It 'rejects a missing session instead of declaring success' {
        & $Engine -NoProfile -NonInteractive -File $Script -Command Status `
            -Session (Join-Path $TestDrive 'absent') -Python $Python 2>$null
        $LASTEXITCODE | Should -Be 1
    }

    It 'rejects Prepare without an explicit source' {
        & $Engine -NoProfile -NonInteractive -File $Script -Command Prepare `
            -Session (Join-Path $TestDrive 'new') -Python $Python 2>$null
        $LASTEXITCODE | Should -Be 1
        Test-Path -LiteralPath (Join-Path $TestDrive 'new') | Should -BeFalse
    }

    It 'preserves a failed status exit through paths with spaces' {
        $Session = Join-Path $TestDrive 'failed session'
        New-Item -ItemType Directory -Path $Session | Out-Null
        $Record = @{ schema = 'wavehelm-local-staging-v1'; session = $Session;
                     state = 'FAILED'; release_readiness = 'NOT_VERIFIED' }
        $Record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Session 'state.json') -Encoding UTF8
        & $Engine -NoProfile -NonInteractive -File $Script -Command Status `
            -Session $Session -Python $Python 2>$null
        $LASTEXITCODE | Should -Be 1
    }

    It 'reports cancelled status without claiming qualification' {
        $Session = Join-Path $TestDrive 'cancelled session'
        New-Item -ItemType Directory -Path $Session | Out-Null
        $Record = @{ schema = 'wavehelm-local-staging-v1'; session = $Session;
                     state = 'CANCELLED'; release_readiness = 'NOT_VERIFIED' }
        $Record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Session 'state.json') -Encoding UTF8
        $Output = & $Engine -NoProfile -NonInteractive -File $Script -Command Status `
            -Session $Session -Python $Python 2>$null
        $LASTEXITCODE | Should -Be 0
        ($Output | ConvertFrom-Json).release_readiness | Should -Be 'NOT_VERIFIED'
    }
}
