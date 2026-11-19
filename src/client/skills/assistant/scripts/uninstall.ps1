[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ioalab/client'),
    [switch]$ConfirmRemoval,
    [switch]$ClearSharedCaches
)

$ErrorActionPreference = 'Stop'

function Assert-PlainPath([string]$Path) {
    for ($part = $Path; $part; $part = [IO.Path]::GetDirectoryName($part)) {
        $item = Get-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        if ($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw ('Linked path requires manual review: '+$part)
        }
    }
}

function Inside([string]$Path, [string]$Directory) {
    $Path.StartsWith($Directory.TrimEnd('\')+'\', [StringComparison]::OrdinalIgnoreCase)
}

function Assert-PlainTree([string]$Path) {
    Assert-PlainPath $Path
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($Path)
    while ($pending.Count) {
        foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($pending.Pop())) {
            $attributes = [IO.File]::GetAttributes($entry)
            if ($attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw ('Linked entry requires manual review: '+$entry)
            }
            if ($attributes -band [IO.FileAttributes]::Directory) { $pending.Push($entry) }
        }
    }
}

function Assert-Stopped([string[]]$Paths) {
    foreach ($process in (Get-CimInstance Win32_Process)) {
        foreach ($path in $Paths) {
            if (($process.ExecutablePath -and (Inside $process.ExecutablePath $path)) -or
                ($process.Name -match '^(python[^.]*|uv|git)\.exe$' -and $process.CommandLine -and
                 $process.CommandLine.IndexOf(($path+'\'), [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                throw ('A process still uses an uninstall path. Close it and retry. PID '+$process.ProcessId)
            }
        }
    }
}

function Get-ClientLock([string]$Data) {
    if (!(Test-Path -LiteralPath $Data -PathType Container)) { return }
    $lockPath = Join-Path $Data 'client.lock'
    Assert-PlainPath $lockPath
    $file = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'ReadWrite')
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        while ($true) {
            try { $file.Lock(0,1); return $file }
            catch [IO.IOException] {
                if ([DateTime]::UtcNow -ge $deadline) { throw 'The original client still holds its data lock. Retry after it stops.' }
                Start-Sleep -Milliseconds 250
            }
        }
    } catch {
        $file.Dispose()
        throw
    }
}

function Get-BoundHooks([string]$Hooks, [string]$Python) {
    if (!(Test-Path -LiteralPath $Hooks -PathType Leaf)) { return }
    Assert-PlainPath $Hooks
    $document = Get-Content -LiteralPath $Hooks -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($group in $document.hooks.PreToolUse) {
        foreach ($entry in $group.hooks) {
            if ($entry.command -and $entry.command.Contains('ioalab_codex.native_hook') -and
                $entry.command.IndexOf($Python, [StringComparison]::OrdinalIgnoreCase) -ge 0) { $entry }
        }
    }
}

try {
    $profileRoot = [Environment]::GetFolderPath('UserProfile')
    $defaultRoot = Join-Path $profileRoot '.ioalab'
    $defaultClient = [IO.Path]::GetFullPath((Join-Path $defaultRoot 'client'))
    $directory = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    if (!$directory -or (Inside $profileRoot $directory) -or $directory -in @($profileRoot,$defaultRoot,[IO.Path]::GetPathRoot($directory).TrimEnd('\'))) {
        throw 'Select the client installation directory.'
    }
    if ($ClearSharedCaches -and $directory -ine $defaultClient) { throw 'Shared caches can only be cleared with the default client.' }
    Assert-PlainPath $directory
    $marker = Join-Path $directory 'ioalab-bootstrap.json'
    $python = Join-Path $directory 'env/Scripts/python.exe'
    $python = [IO.Path]::GetFullPath($python)
    $present = Test-Path -LiteralPath $directory
    $config = Join-Path $directory 'settings.local.json'
    $data = $null
    if ($present) {
        Assert-PlainPath $marker
        if (!(Test-Path -LiteralPath $marker -PathType Leaf)) { throw 'This directory has no IoA Lab installation marker.' }
        $record = Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($record.format -ne 2 -or $record.phase -notin @('completed','installing','upgrading') -or
            $record.commit -cnotmatch '^[a-f0-9]{40}$' -or $record.repository -notin @(
            'https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git','https://github.com/Weizheng96/Jiuwen-IoA-Lab.git')) {
            throw 'The directory does not contain a recognized official IoA Lab installation.'
        }
        if ($record.configurationFile) { $config = [IO.Path]::GetFullPath($record.configurationFile) }
        if (!(Inside $config $directory)) { throw 'The installation uses external configuration. Review and remove it separately.' }
        Assert-PlainPath $config
        if (Test-Path -LiteralPath $config) {
            Assert-PlainPath $python
            $probe = "import json,sys; from ioalab_sdk.runtime import data_directory,read_configuration; print(json.dumps(str(data_directory(sys.argv[1],read_configuration(sys.argv[1])))))"
            $resolved = & $python -I -X utf8 -c $probe $config 2>$null
            if ($LASTEXITCODE -ne 0 -or !$resolved) { throw 'The installed SDK could not validate the data directory.' }
            $data = $resolved | ConvertFrom-Json
            if (!(Inside $data $directory)) { throw 'The installation uses an external data directory. Review and remove it separately.' }
            Assert-PlainPath $data
        }
    }

    $skills = @()
    foreach ($relative in @('.agents/skills','.claude/skills','.codex/skills')) {
        foreach ($name in @('ioalab-delegate','ioalab-provider')) {
            $skill = [IO.Path]::GetFullPath((Join-Path $profileRoot "$relative/$name"))
            $manifest = Join-Path $skill '.ioalab-skill.json'
            if (!(Test-Path -LiteralPath $manifest -PathType Leaf)) { continue }
            Assert-PlainPath $manifest
            $binding = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($binding.format -eq 'ioalab.skill/2' -and $binding.binding.python -ieq $python -and
                $binding.binding.configurationFile -ieq $config) { $skills += $skill }
        }
    }
    $codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $profileRoot '.codex' }
    $hookFile = [IO.Path]::GetFullPath((Join-Path $codexRoot 'hooks.json'))
    $hooks = @(Get-BoundHooks $hookFile $python)
    $targets = @($skills)
    if ($present) { $targets += $directory }
    if ($ClearSharedCaches) {
        $targets += @(foreach ($name in @('bootstrap-tools','access-runtimes')) {
            $path = Join-Path $defaultRoot $name
            if (Test-Path -LiteralPath $path) { $path }
        })
    }
    $result = [ordered]@{status='planned'; paths=$targets; codexHook=($hooks.Count -gt 0)}
    if ($ConfirmRemoval) {
        foreach ($target in $targets) { Assert-PlainTree $target }
        if ($data) {
            $stopArguments = New-TemporaryFile
            try {
                [IO.File]::WriteAllText($stopArguments.FullName, '{}')
                & $python -I -X utf8 -m ioalab_sdk.access call runtime.stop --config $config --args-file $stopArguments.FullName 2>$null | Out-Null
            } finally { Remove-Item -LiteralPath $stopArguments.FullName -Force }
        }
        $lease = if ($data) { Get-ClientLock $data }
        try {
            Assert-Stopped @($targets)
            if ($hooks.Count) {
                $entryFiles = @(foreach ($skill in $skills) {
                    foreach ($name in @('entry-assistant.py','entry-provider.py')) {
                        $candidate = Join-Path $skill "native/$name"
                        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate }
                    }
                })
                if (!$entryFiles.Count) { throw 'The bound Codex hook requires its original native entry for removal.' }
                & $python -I -X utf8 -m ioalab_codex.native_install uninstall --entry $entryFiles[0] 2>$null | Out-Null
                if ($LASTEXITCODE -ne 0 -or @(Get-BoundHooks $hookFile $python).Count) { throw 'The bound Codex hook could not be removed.' }
            }
            if ($present -and (Test-Path -LiteralPath $config)) {
                # Disable fresh starts while the original data lock is held.
                Remove-Item -LiteralPath $config -Force
            }
        } finally {
            if ($lease) { $lease.Dispose() }
        }
        Assert-Stopped @($targets)
        foreach ($target in $targets) {
            if ($target -ieq $directory) {
                # Keep the ownership marker until the rest is gone, so a partial removal can be retried.
                foreach ($item in (Get-ChildItem -LiteralPath $target -Force | Where-Object FullName -NE $marker)) {
                    Remove-Item -LiteralPath $item.FullName -Recurse -Force
                }
                Remove-Item -LiteralPath $marker -Force
                Remove-Item -LiteralPath $target
            } else {
                Remove-Item -LiteralPath $target -Recurse -Force
            }
            if (Test-Path -LiteralPath $target) { throw ('Removal is incomplete: '+$target) }
        }
        if ((Test-Path -LiteralPath $defaultRoot -PathType Container) -and
            !(Get-ChildItem -LiteralPath $defaultRoot -Force | Select-Object -First 1)) {
            Remove-Item -LiteralPath $defaultRoot
        }
        $result.status = 'completed'
    }
    $result | ConvertTo-Json -Depth 4 -Compress
} catch {
    @{status='rejected'; error=$_.Exception.Message} | ConvertTo-Json -Compress
    exit 1
}
