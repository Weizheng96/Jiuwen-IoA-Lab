[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('jiuwen','codex','claude_code')][string]$HostType,
    [string]$HostRef,
    [string]$Commit,
    [string]$Tag,
    [string]$InstallDir = (Join-Path $HOME '.ioalab/client'),
    [string]$SourceDirectory = (Join-Path $HOME ('.ioalab/sources/'+[guid]::NewGuid().ToString('N'))),
    [string]$Proxy,
    [string]$PythonMirror,
    [switch]$ReuseInstalled,
    [switch]$ManagedPython
)
$ErrorActionPreference = 'Stop'
$stage = 'preflight'
$tools = Join-Path $HOME '.ioalab/bootstrap-tools'
$gitAsset = @{name='MinGit-2.56.1-64-bit.zip'; directory='git-2.56.1'; executable='cmd/git.exe';
    url='https://github.com/git-for-windows/git/releases/download/v2.56.1.windows.1/MinGit-2.56.1-64-bit.zip';
    hash='4a92850b6e917da4fd010669b614b55dccf5b0ec333a534cc4be4700dfefdafd'}
$uvAsset = @{name='uv-x86_64-pc-windows-msvc.zip'; directory='uv-0.9.21'; executable='uv.exe';
    url='https://github.com/astral-sh/uv/releases/download/0.9.21/uv-x86_64-pc-windows-msvc.zip';
    hash='d27952e73183ef8f6ee8c2a50cf8b3f2e08e01b6a9279a00a85cb261ea8d8337'}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    $result = & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw ('Command failed at '+$stage+'; exit '+$LASTEXITCODE) }
    $result
}
function Get-PortableTool($Asset) {
    if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This portable tool manifest covers Windows x64; preserve the installation and use the official tool for this architecture' }
    New-Item -ItemType Directory -Force -Path $tools | Out-Null
    $archive = Join-Path $tools $Asset.name
    $destination = Join-Path $tools $Asset.directory
    # Retained downloads are verified every time, before extraction or execution.
    if (!(Test-Path -LiteralPath $archive)) {
        $options = @{Uri=$Asset.url; OutFile=$archive; UseBasicParsing=$true}
        if ($Proxy) { $options.Proxy=$Proxy }
        Invoke-WebRequest @options
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant() -ne $Asset.hash) {
        throw 'Official portable archive checksum mismatch; preserve it for diagnosis'
    }
    # Fresh extraction prevents an unverified cached executable from being reused.
    if (Test-Path -LiteralPath $destination) { $destination += '-'+[guid]::NewGuid().ToString('N') }
    Expand-Archive -LiteralPath $archive -DestinationPath $destination
    $executable = Join-Path $destination $Asset.executable
    if (!(Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Portable tool executable is missing' }
    $executable
}

try {
    if ($ManagedPython -and ($ReuseInstalled -or (Test-Path -LiteralPath (Join-Path $InstallDir 'ioalab-bootstrap.json')))) {throw 'Do not replace a valid installed client interpreter; explicit safe upgrade remains required'}
    if (!!$Commit -eq !!$Tag) { throw 'Choose either the actual GitHub develop README commit or a published GitCode tag' }
    if ($Commit -and $Commit -notmatch '^[0-9a-f]{40}$') { throw 'Use the full fixed README source commit' }
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
    $stage='checkout'
    if (Test-Path -LiteralPath $SourceDirectory) {throw 'Source destination exists; preserve it and select an unused directory'}
    $repository=if ($Commit) {'https://github.com/Weizheng96/Jiuwen-IoA-Lab.git'} else {'https://gitcode.com/WeiZheng96/jiuwen-IoA-lab.git'}
    $revision=if ($Commit) {'develop'} else {$Tag}
    $gitOptions=@()
    if ($Proxy) {$gitOptions=@('-c','http.proxy='+$Proxy)}
    Invoke-Checked $git ($gitOptions+@('clone','--depth','1','--filter=blob:none','--sparse','--single-branch','--branch',$revision,$repository,$SourceDirectory)) | Out-Null
    Invoke-Checked $git ($gitOptions+@('-C',$SourceDirectory,'sparse-checkout','set','src/client','src/a2c')) | Out-Null
    $resolved=(Invoke-Checked $git ($gitOptions+@('-C',$SourceDirectory,'rev-parse','HEAD'))).Trim()
    if ($Commit -and $resolved -ne $Commit) {throw 'Development source moved; reread the actual README source and preserve this checkout'}
    $stage='source-verification'
    $published=(Invoke-Checked $git ($gitOptions+@('-C',$SourceDirectory,'show',($resolved+':src/client/bootstrap.ps1')))) -join "`n"
    $running=[IO.File]::ReadAllText($PSCommandPath).Replace("`r`n","`n").TrimEnd("`n")
    if ($published.TrimEnd("`n") -cne $running) {throw 'Running launcher differs from its selected fixed Git object'}
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
        $options=@('--no-config','python','install','3.12','--install-dir',(Join-Path $tools 'python'),'--no-bin','--no-registry')
        if ($PythonMirror) {$options+=@('--mirror',$PythonMirror)}
        $previousProxy=$env:HTTPS_PROXY
        try {
            if ($Proxy) {$env:HTTPS_PROXY=$Proxy}
            Invoke-Checked $uv $options | Out-Null
        } finally {$env:HTTPS_PROXY=$previousProxy}
        # Use the same private installation directory, never project/venv discovery.
        $previousPythonDirectory=$env:UV_PYTHON_INSTALL_DIR
        try {
            $env:UV_PYTHON_INSTALL_DIR=Join-Path $tools 'python'
            $python=(Invoke-Checked $uv @('--no-config','python','find','--managed-python','--no-project','--no-python-downloads','3.12')).Trim()
        } finally { $env:UV_PYTHON_INSTALL_DIR=$previousPythonDirectory }
        if (!$python) {throw 'Managed interpreter location is missing'}
    }
    $stage='client-install'
    $arguments=@('-I',(Join-Path $SourceDirectory 'src/client/bootstrap.py'),'--git',$git,'--install-dir',$InstallDir,'--host',$HostType,'--host-ref',$HostRef)
    if ($Proxy) {$arguments+=@('--proxy',$Proxy)}
    $arguments+=if ($Commit) {@('--commit',$Commit)} else {@('--tag',$Tag)}
    if ($ReuseInstalled) {$arguments+='--reuse-installed'}
    $result=& $python @arguments
    $code=$LASTEXITCODE
    if ($code -ne 0) {
        try {
            $packet=$result | ConvertFrom-Json
            if ($packet.error.code -eq 'unsupported_python') {
                $unused=$InstallDir+'-python312-'+[guid]::NewGuid().ToString('N')
                $packet.error | Add-Member -NotePropertyName recoveryInstruction -NotePropertyValue ('Preserve this failed directory and install.log. Explicitly rerun this same fixed launcher with -ManagedPython -InstallDir "'+$unused+'"; do not change an existing valid account or configuration.')
                $revisionArgument=if ($Commit) {' -Commit '+$Commit} else {' -Tag '+$Tag}
                $packet.error | Add-Member -NotePropertyName recoveryCommand -NotePropertyValue ('powershell -NoProfile -ExecutionPolicy RemoteSigned -File "'+$PSCommandPath+'" -HostType '+$HostType+' -HostRef '+$HostRef+$revisionArgument+' -ManagedPython -InstallDir "'+$unused+'"')
            }
            $packet | ConvertTo-Json -Depth 8
        } catch {throw 'Client install failed; preserve the installation and private install.log'}
        exit 1
    }
    $result
} catch {
    @{status='rejected';data=@{stage=$stage; connected=$false; sourceDirectory=$SourceDirectory; clientDirectory=$InstallDir};
      error=@{code='environment_setup_failed';message=$_.Exception.Message;recovery='preserve_installation_and_use_explicit_official_source_or_proxy'}} | ConvertTo-Json -Depth 4
    exit 1
}
