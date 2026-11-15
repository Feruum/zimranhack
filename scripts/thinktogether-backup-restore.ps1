[CmdletBinding()]
param(
    [string]$Container = 'thinktogether-db-1',
    [string]$Database = 'thinktogether',
    [string]$OutputDirectory,
    [switch]$KeepRestoredDatabase
)
$ErrorActionPreference = 'Stop'
# This local drill deliberately cannot target a reference or production database.
if ($Container -cne 'thinktogether-db-1' -or $Database -cne 'thinktogether') {
    throw 'This drill accepts only the isolated local ThinkTogether container/database.'
}
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repositoryRoot '.learnhouse/thinktogether-backups' }
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $repositoryRoot '.learnhouse'))
if (-not $outputPath.StartsWith($allowedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Backup output must be inside this worktree .learnhouse directory.'
}
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$drillId = [guid]::NewGuid().ToString('N')
$temporaryDatabase = 'tt_restore_' + $drillId
$containerDump = '/tmp/tt-backup-' + $drillId + '.dump'
$hostDump = Join-Path $outputPath ($drillId + '.dump')
$evidencePath = Join-Path $outputPath ($drillId + '.json')
if ($temporaryDatabase -notmatch '^tt_restore_[0-9a-f]{32}$' -or $temporaryDatabase -ceq $Database -or $containerDump -notmatch '^/tmp/tt-backup-[0-9a-f]{32}\.dump$') {
    throw 'Temporary restore target validation failed.'
}

function Invoke-CheckedDocker {
    param([string[]]$DockerArguments)
    $result = & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker database operation failed; backup/restore is not verified.' }
    return $result
}

function Get-DatabaseCounts {
    param([string]$TargetDatabase)
    # Identifiers come from PostgreSQL's catalog and are quoted by PostgreSQL.
    $countSql = @'
SELECT format('SELECT %L AS table_name, count(*) AS row_count FROM %I.%I', tablename, schemaname, tablename)
FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename
\gexec
SELECT 'revision', version_num FROM alembic_version;
'@
    $result = $countSql | & docker exec -i $Container psql -U thinktogether -d $TargetDatabase -v ON_ERROR_STOP=1 -At -F '|'
    if ($LASTEXITCODE -ne 0) { throw 'Could not collect database counts.' }
    return @($result | Where-Object { $_ -match '\|' } | Sort-Object)
}

$restoreCreated = $false
$passed = $false
$started = [DateTimeOffset]::UtcNow
try {
    $before = Get-DatabaseCounts $Database
    Invoke-CheckedDocker @('exec', $Container, 'pg_dump', '-U', 'thinktogether', '-d', $Database, '--format=custom', '--no-owner', '--no-acl', '--file', $containerDump) | Out-Null
    Invoke-CheckedDocker @('cp', ($Container + ':' + $containerDump), $hostDump) | Out-Null
    if ((Get-Item -LiteralPath $hostDump).Length -lt 1024) { throw 'Backup archive is unexpectedly small.' }
    Invoke-CheckedDocker @('exec', $Container, 'createdb', '-U', 'thinktogether', '--template=template0', $temporaryDatabase) | Out-Null
    $restoreCreated = $true
    Invoke-CheckedDocker @('exec', $Container, 'pg_restore', '-U', 'thinktogether', '-d', $temporaryDatabase, '--exit-on-error', '--no-owner', '--no-acl', $containerDump) | Out-Null
    $restored = Get-DatabaseCounts $temporaryDatabase
    $after = Get-DatabaseCounts $Database
    if (@(Compare-Object $before $after).Count -or @(Compare-Object $before $restored).Count) {
        throw 'Row counts changed during the drill or did not restore exactly; quiesce local writes and rerun.'
    }
    $passed = $true
    $evidence = [ordered]@{
        passed = $true
        started_utc = $started.ToString('o')
        finished_utc = [DateTimeOffset]::UtcNow.ToString('o')
        source_container = $Container
        source_database = $Database
        restored_database = $temporaryDatabase
        archive = $hostDump
        sha256 = (Get-FileHash -LiteralPath $hostDump -Algorithm SHA256).Hash
        bytes = (Get-Item -LiteralPath $hostDump).Length
        catalog_and_row_counts = $restored
        scope = 'Local PostgreSQL archive and separate database restore; does not validate Neon PITR or R2 objects.'
    }
    [IO.File]::WriteAllText($evidencePath, ($evidence | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
    Write-Output ('PASS: restored and compared ' + ($restored.Count - 1) + ' tables in separate database ' + $temporaryDatabase)
    Write-Output ('Evidence: ' + $evidencePath)
} finally {
    # Exact validated temporary database only; the source is never dropped.
    if ($restoreCreated -and (-not $KeepRestoredDatabase -or -not $passed)) {
        Invoke-CheckedDocker @('exec', $Container, 'dropdb', '-U', 'thinktogether', '--if-exists', $temporaryDatabase) | Out-Null
    }
    Invoke-CheckedDocker @('exec', $Container, 'rm', '-f', $containerDump) | Out-Null
}
