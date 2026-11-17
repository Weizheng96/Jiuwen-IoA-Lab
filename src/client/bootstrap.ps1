[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('jiuwen','codex','claude_code')][string]$HostType,
    [string]$HostRef,
    [string]$Channel = 'main',
    [string]$Commit,
    [string]$Tag,
    [string]$InstallDir = (Join-Path $HOME '.ioalab/client'),
    [string]$Proxy,
    [string]$PythonMirror,
    [switch]$Upgrade,
    [switch]$ReuseInstalled,
    [switch]$ManagedPython
)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 renders download progress per chunk; large archives otherwise crawl.
$ProgressPreference = 'SilentlyContinue'
$stage = 'preflight'
$resolved = $null
# The source is the official clone this launcher runs from (the one-sentence `git clone --depth 1`).
$clone = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$officialOrigins = @{main='https://gitcode.com/WeiZheng96/jiuwen-IoA-lab'; develop='https://github.com/Weizheng96/Jiuwen-IoA-Lab'}
$tools = Join-Path $HOME '.ioalab/bootstrap-tools'
# Official sources first; registered domestic mirrors serve the same files under the same fixed SHA256.
$gitAsset = @{name='MinGit-2.56.1-64-bit.zip'; directory='git-2.56.1'; executable='cmd/git.exe';
    url='https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/MinGit-2.56.1-64-bit.zip';
    mirrors=@('https://registry.npmmirror.com/-/binary/git-for-windows/v2.56.1.windows.1/MinGit-2.56.1-64-bit.zip');
    hash='4a92850b6e917da4fd010669b614b55dccf5b0ec333a534cc4be4700dfefdafd'}
$uvAsset = @{name='uv-x86_64-pc-windows-msvc.zip'; directory='uv-0.9.21'; executable='uv.exe';
    url='https://github.com/astral-sh/uv/releases/download/0.9.21/uv-x86_64-pc-windows-msvc.zip';
    mirrors=@('https://registry.npmmirror.com/-/binary/uv/0.9.21/uv-x86_64-pc-windows-msvc.zip');
    hash='d27952e73183ef8f6ee8c2a50cf8b3f2e08e01b6a9279a00a85cb261ea8d8337'}
# uv verifies each interpreter archive against its own pinned checksums whichever mirror serves it.
$pythonMirrors = @('https://registry.npmmirror.com/-/binary/python-build-standalone/')
# Fixed ioalab.onboarding/1 six-item handoff; escaped so the launcher itself stays ASCII.
$handoffText = @(
    @('\u63a5\u5165\u72b6\u6001','\u73af\u5883\u51c6\u5907\u5931\u8d25\uff1b\u4fdd\u7559\u73b0\u573a\uff0c\u5c1a\u672a\u8fde\u63a5'),
    @('\u5ba2\u6237\u7aef\u4f4d\u7f6e',''),
    @('\u8d26\u53f7\u4e0e\u51ed\u636e','\u672a\u8bfb\u53d6\uff1b\u901a\u8fc7\u539f\u7ed1\u5b9aaccount.setup\u672c\u673a\u9875\u67e5\u770b'),
    @('\u53cc\u89d2\u8272Skill','\u672a\u5bfc\u51fa\u6216\u672a\u5b89\u88c5\uff1b\u4fdd\u7559\u539f\u5b89\u88c5'),
    @('\u5bbf\u4e3b\u4e0e\u901a\u77e5','\uff1b\u9ed8\u8ba4\u4e0e\u901a\u77e5\u672a\u6838\u5b9e'),
    @('\u5b98\u7f51','\u4f7f\u7528\u539f\u914d\u7f6e\u7684\u5e73\u53f0\u53ca\u540c\u8d26\u53f7\u8fdb\u5165\u6211\u7684\u667a\u80fd\u4f53\uff1b\u914d\u7f6e\u672a\u8bfb\u53d6')
)

function ConvertTo-AsciiJson($Value) {
    $json = $Value | ConvertTo-Json -Depth 10 -Compress
    [regex]::Replace($json, '[^\x00-\x7F]', { param($match) '\u{0:x4}' -f [int][char]$match.Value })
}
function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    $result = & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Command failed at '+$stage+'; exit '+$LASTEXITCODE) }
    $result
}
function Save-VerifiedDownload($Asset, [string]$Archive) {
    $failed = @()
    foreach ($url in @($Asset.url) + @($Asset.mirrors)) {
        $partial = $Archive+'.partial-'+[guid]::NewGuid().ToString('N')
        try {
            $options = @{Uri=$url; OutFile=$partial; UseBasicParsing=$true}
            if ($Proxy) { $options.Proxy=$Proxy }
            Invoke-WebRequest @options
        } catch {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
            $failed += ([uri]$url).Host
            continue
        }
        # Any source must produce the identical fixed file; a mismatch is refused, never retried elsewhere.
        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $partial).Hash.ToLowerInvariant() -ne $Asset.hash) {
            throw ('Archive from '+([uri]$url).Host+' does not match the fixed SHA256; preserved for diagnosis at '+$partial)
        }
        Move-Item -LiteralPath $partial -Destination $Archive
        return
    }
    throw ('Official and registered mirror downloads failed ('+($failed -join ', ')+'); supply an explicit -Proxy and retry; checksums stay enforced')
}
function Get-PortableTool($Asset) {
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This portable tool manifest covers Windows x64; preserve the installation and use the official tool for this architecture' }
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    $archive = Join-Path $tools $Asset.name
    $destination = Join-Path $tools $Asset.directory
    # Retained downloads are verified every time, before extraction or execution.
    if (!(Test-Path -LiteralPath $archive)) { Save-VerifiedDownload $Asset $archive }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant() -ne $Asset.hash) {
        throw 'Retained portable archive checksum mismatch; preserve it for diagnosis'
    }
    # Fresh extraction prevents an unverified cached executable from being reused.
    if (Test-Path -LiteralPath $destination) { $destination += '-'+[guid]::NewGuid().ToString('N') }
    Expand-Archive -LiteralPath $archive -DestinationPath $destination
    $executable = Join-Path $destination $Asset.executable
    if (!(Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Portable tool executable is missing' }
    $executable
}

try {
    if ($Upgrade -and $ReuseInstalled) {throw 'Choose either -ReuseInstalled or an explicit -Upgrade'}
    if ($ManagedPython -and !$Upgrade -and ($ReuseInstalled -or (Test-Path -LiteralPath (Join-Path $InstallDir 'ioalab-bootstrap.json')))) {throw 'Do not replace a valid installed client interpreter; use an explicit -Upgrade'}
    if ($Commit -and $Tag) { throw 'Choose a published GitCode -Tag or a -Channel commit, not both' }
    if ($Tag -and $PSBoundParameters.ContainsKey('Channel')) { throw 'A published GitCode tag takes no -Channel' }
    if ($Channel -cnotin @('main','develop')) { throw 'Channel must be main (GitCode formal source) or develop (GitHub development verification)' }
    if ($Commit -and $Commit -notmatch '^[0-9a-f]{40}$') { throw 'Use the full fixed source commit' }
    if ($Tag -and ($Tag -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$' -or $Tag.Contains('..'))) {throw 'Invalid tag'}
    if (!$HostRef) { $HostRef=$HostType }
    if ($HostRef -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$') {throw 'Invalid stable host reference'}
    foreach ($value in @($Proxy,$PythonMirror)) {
        if ($value -and ([uri]$value).Scheme -notin @('http','https')) {throw 'Explicit proxy/mirror requires an HTTP(S) URL'}
    }
    if ($PythonMirror -and ([uri]$PythonMirror).Scheme -ne 'https') {throw 'Python mirror requires HTTPS; checksum verification remains enabled'}
    $stage='git'
    $gitCommand=Get-Command git -ErrorAction SilentlyContinue
    $git=if ($gitCommand) {$gitCommand.Source} else {Get-PortableTool $gitAsset}
    Invoke-Checked $git @('--version') | Out-Null
    $stage='source'
    # No second clone: verify the official clone around this launcher; --depth 1 is enough.
    $top=(Invoke-Checked $git @('-C',$clone,'rev-parse','--show-toplevel')).Trim()
    if ([IO.Path]::GetFullPath($top).TrimEnd('\','/') -ne $clone.TrimEnd('\','/')) {throw 'Run src/client/bootstrap.ps1 from inside the official clone'}
    $origin=(Invoke-Checked $git @('-C',$clone,'remote','get-url','origin')).Trim().TrimEnd('/') -replace '\.git$',''
    $expectedOrigin=if ($Tag) {$officialOrigins.main} else {$officialOrigins[$Channel]}
    if ($origin -ne $expectedOrigin) {throw ('This clone is not from '+$expectedOrigin+'; clone the official repository for -Channel '+$Channel)}
    $resolved=(Invoke-Checked $git @('-C',$clone,'rev-parse','HEAD')).Trim()
    if ($resolved -notmatch '^[0-9a-f]{40}$') {throw 'The clone HEAD did not resolve to a full commit'}
    if ($Commit -and $resolved -ne $Commit) {throw 'The clone HEAD is not the requested commit; clone again and preserve this checkout'}
    $stage='source-verification'
    $published=(Invoke-Checked $git @('-C',$clone,'show',($resolved+':src/client/bootstrap.ps1'))) -join "`n"
    $running=[IO.File]::ReadAllText($PSCommandPath).Replace("`r`n","`n").TrimEnd("`n")
    if ($published.TrimEnd("`n") -cne $running) {throw 'Running launcher differs from its clone HEAD Git object'}
    $stage='python'
    $python=$null
    $probe='import sys,json; print(json.dumps({"version":list(sys.version_info[:2]),"base":getattr(sys,"_base_executable",sys.executable)}))'
    foreach ($name in @('python','python3','py')) {
        $candidate=Get-Command $name -ErrorAction SilentlyContinue
        if (!$candidate) {continue}
        try {
            $options=if ($name -eq 'py') {@('-3','-I','-c',$probe)} else {@('-I','-c',$probe)}
            $found=(Invoke-Checked $candidate.Source $options) | ConvertFrom-Json
            if ($found.version[0] -gt 3 -or ($found.version[0] -eq 3 -and $found.version[1] -ge 11)) {$python=$found.base; break}
        } catch {continue}
    }
    if (!$python -or $ManagedPython) {
        $uvCommand=Get-Command uv -ErrorAction SilentlyContinue
        $uv=if ($uvCommand) {$uvCommand.Source} else {Get-PortableTool $uvAsset}
        # An explicit user mirror is used alone; otherwise official first, then registered mirrors.
        $sources=if ($PythonMirror) {@($PythonMirror)} else {@('') + $pythonMirrors}
        $installed=$false
        foreach ($mirror in $sources) {
            $options=@('--no-config','python','install','3.12','--install-dir',(Join-Path $tools 'python'),'--no-bin','--no-registry')
            if ($mirror) {$options+=@('--mirror',$mirror)}
            $previousProxy=$env:HTTPS_PROXY
            try {
                if ($Proxy) {$env:HTTPS_PROXY=$Proxy}
                Invoke-Checked $uv $options | Out-Null
                $installed=$true; break
            } catch {continue} finally {$env:HTTPS_PROXY=$previousProxy}
        }
        if (!$installed) {throw 'Private Python 3.12 download failed from official and registered mirrors; supply an explicit -Proxy or -PythonMirror'}
        # Use the same private installation directory, never project/venv discovery.
        $previousPythonDirectory=$env:UV_PYTHON_INSTALL_DIR
        try {
            $env:UV_PYTHON_INSTALL_DIR=Join-Path $tools 'python'
            $python=(Invoke-Checked $uv @('--no-config','python','find','--managed-python','--no-project','--no-python-downloads','3.12')).Trim()
        } finally { $env:UV_PYTHON_INSTALL_DIR=$previousPythonDirectory }
        if (!$python) {throw 'Managed interpreter location is missing'}
    }
    $stage='client-install'
    $arguments=@('-I',(Join-Path $clone 'src/client/bootstrap.py'),'--git',$git,'--install-dir',$InstallDir,'--host',$HostType,'--host-ref',$HostRef)
    if ($Proxy) {$arguments+=@('--proxy',$Proxy)}
    $arguments+=if ($Tag) {@('--tag',$Tag)} else {@('--commit',$resolved,'--channel',$Channel)}
    if ($ReuseInstalled) {$arguments+='--reuse-installed'}
    if ($Upgrade) {$arguments+='--upgrade'}
    $result=& $python @arguments
    $code=$LASTEXITCODE
    if ($code -ne 0) {
        try {
            $packet=$result | ConvertFrom-Json
            if ($packet.error.code -eq 'unsupported_python') {
                $unused=$InstallDir+'-python312-'+[guid]::NewGuid().ToString('N')
                $packet.error | Add-Member -NotePropertyName recoveryInstruction -NotePropertyValue ('Preserve this failed directory and install.log. Explicitly rerun this same fixed launcher with -ManagedPython -InstallDir "'+$unused+'"; do not change an existing valid account or configuration.')
                $revisionArgument=if ($Tag) {' -Tag '+$Tag} else {' -Channel '+$Channel+' -Commit '+$resolved}
                $packet.error | Add-Member -NotePropertyName recoveryCommand -NotePropertyValue ('powershell -NoProfile -ExecutionPolicy RemoteSigned -File "'+$PSCommandPath+'" -HostType '+$HostType+' -HostRef '+$HostRef+$revisionArgument+' -ManagedPython -InstallDir "'+$unused+'"')
            }
            ConvertTo-AsciiJson $packet
        } catch {throw 'Client install failed; preserve the installation and private install.log'}
        exit 1
    }
    $result
} catch {
    $handoff=@(foreach ($item in $handoffText) {[ordered]@{section=[regex]::Unescape($item[0]); value=[regex]::Unescape($item[1])}})
    $handoff[1].value=$InstallDir
    $handoff[4].value=$HostType+$handoff[4].value
    ConvertTo-AsciiJson ([ordered]@{status='rejected';
        data=[ordered]@{format='ioalab.onboarding/1'; handoff=$handoff; stage=$stage; connected=$false;
                        resolvedCommit=$resolved; sourceClone=$clone; clientDirectory=$InstallDir};
        error=[ordered]@{code='environment_setup_failed'; message=$_.Exception.Message;
                         recovery='preserve_installation_and_use_explicit_official_source_or_proxy'}})
    exit 1
}
