$ErrorActionPreference = 'Stop'
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$destination = Join-Path $repositoryRoot 'apps/web/public/thinktogether-source.tar.gz'
$manifest = Join-Path ([IO.Path]::GetTempPath()) ('tt-source-' + [guid]::NewGuid().ToString('N') + '.txt')
Push-Location $repositoryRoot
try {
    # Include the working implementation as well as tracked sources; git archive
    # alone would omit an uncommitted fork. Git excludes .env and local evidence.
    $sourcePaths = @(& git -c core.quotePath=false ls-files --cached --others --exclude-standard)
    if ($LASTEXITCODE -ne 0 -or $sourcePaths.Count -lt 100) { throw 'Could not inventory corresponding source' }
    # Keep local evidence and the generated archive out even if accidentally
    # staged: --exclude-standard only applies to untracked files.
    $sourcePaths = @($sourcePaths | Where-Object {
        $_ -notmatch '(^|/)(\.git|\.learnhouse|\.worktrees|\.frontend-workbench|node_modules)(/|$)' -and
        $_ -ne 'apps/web/public/thinktogether-source.tar.gz'
    })
    foreach ($sourcePath in $sourcePaths) {
        if (($sourcePath -match '(^|/)\.env' -and $sourcePath -notmatch '\.example$') -or $sourcePath.StartsWith('../')) {
            throw 'Refusing to package a local environment or an out-of-tree path'
        }
    }
    # Deleted tracked files are still reported by ls-files until the change is
    # committed. Archive the current working tree, excluding those removals.
    $sourcePaths = @($sourcePaths | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    [IO.File]::WriteAllLines($manifest, $sourcePaths, [Text.UTF8Encoding]::new($false))
    & tar -czf $destination -C $repositoryRoot -T $manifest
    if ($LASTEXITCODE -ne 0) { throw 'Source archive failed' }
    Write-Output "Corresponding source archive: $destination"
} finally {
    Pop-Location
    if (Test-Path -LiteralPath $manifest) { Remove-Item -LiteralPath $manifest }
}
