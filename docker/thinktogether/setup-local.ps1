param([switch]$Start)
$ErrorActionPreference = 'Stop'
$envPath = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    function New-Secret { [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant() }
    $databasePassword = New-Secret
    $replacements = @{
        LEARNHOUSE_INITIAL_ADMIN_PASSWORD = ('Tt!' + (New-Secret).Substring(0,24))
        LEARNHOUSE_AUTH_JWT_SECRET_KEY = (New-Secret)
        NEXTAUTH_SECRET = (New-Secret)
        COLLAB_INTERNAL_KEY = (New-Secret)
        TT_INTERNAL_KEY = (New-Secret)
        POSTGRES_PASSWORD = $databasePassword
        LEARNHOUSE_SQL_CONNECTION_STRING = "postgresql://thinktogether:${databasePassword}@db:5432/thinktogether"
    }
    $lines = Get-Content -LiteralPath (Join-Path $PSScriptRoot '.env.example')
    $lines = foreach ($line in $lines) {
        if ($line -match '^([^#=]+)=' -and $replacements.ContainsKey($Matches[1])) {
            $entryName = $Matches[1]
            "$entryName=$($replacements[$entryName])"
        } else { $line }
    }
    [IO.File]::WriteAllText($envPath, (($lines -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
    Write-Output 'Created local configuration with new secrets. Credentials remain in .env.'
} else {
    Write-Output 'Keeping the existing local configuration.'
}
if ($Start) {
    & docker compose --project-directory $PSScriptRoot -f (Join-Path $PSScriptRoot 'docker-compose.yml') up -d --build --wait --wait-timeout 300
    exit $LASTEXITCODE
}
