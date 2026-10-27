param(
    [switch]$SyncKey,
    [switch]$Connect,
    [switch]$PrepareOnly
)

$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
Set-Location -LiteralPath $taskRoot

function Read-KeyFile([string]$Path) {
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path, [Text.Encoding]::UTF8)) {
        if (-not $line.Trim() -or $line.TrimStart().StartsWith('#')) { continue }
        if ($line.Trim() -match '^(AstrBot|NapCat):\s*https?://\S+$') { continue }
        $parts = $line.Split([char[]]'=', 2, [StringSplitOptions]::None)
        if ($parts.Count -ne 2 -or $values.ContainsKey($parts[0].Trim())) {
            throw 'Invalid or duplicate field in key.txt. Follow key.example.txt.'
        }
        $values[$parts[0].Trim()] = $parts[1].Trim()
    }
    return $values
}

function Invoke-SshKeyTool([string]$Program, [string[]]$ToolArguments, [int[]]$SuccessCodes = @(0)) {
    $savedPreference = $ErrorActionPreference
    try {
        # Discovery may report an authentication refusal after saving the host key.
        $ErrorActionPreference = 'Continue'
        $output = @(& $Program @ToolArguments 2>$null)
        $code = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    if ($SuccessCodes -notcontains $code) {
        throw "$Program failed during SSH host-key verification (exit $code). Check the server address, port 22 and network."
    }
    return $output
}

function Confirm-SshHostKey([string]$Server, [string]$KnownHosts, [string]$TempDirectory, [string]$User = 'root') {
    $newKeyFile = Join-Path $TempDirectory 'host-key.pub'
    $oldKeyFile = Join-Path $TempDirectory 'previous-host-keys'
    try {
        # Windows ssh-keyscan 9.5 cannot negotiate some Ubuntu 26.04 KEX algorithms.
        # Discover into a private temporary file with every credential method disabled.
        [IO.File]::WriteAllText($newKeyFile, '', [Text.UTF8Encoding]::new($false))
        Push-Location -LiteralPath $TempDirectory
        try {
            Invoke-SshKeyTool 'ssh.exe' @('-p', '22', '-o', 'ConnectTimeout=10', '-o', 'ConnectionAttempts=1',
                '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile=host-key.pub',
                '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', 'UpdateHostKeys=no', '-o', 'HashKnownHosts=no',
                '-o', "HostKeyAlias=$Server",
                '-o', 'ControlPath=none', '-o', 'BatchMode=yes', '-o', 'PreferredAuthentications=none',
                '-o', 'PubkeyAuthentication=no', '-o', 'PasswordAuthentication=no',
                '-o', 'KbdInteractiveAuthentication=no', "${User}@${Server}", 'exit') @(0, 255) | Out-Null
        } finally { Pop-Location }
        $scanned = [IO.File]::ReadAllLines($newKeyFile)
        $newKeys = @($scanned | Where-Object { $_ -match '^\S+\s+ssh-ed25519\s+[A-Za-z0-9+/]+={0,2}\s*$' } |
            ForEach-Object { "${Server} ssh-ed25519 $(($_ -split '\s+')[2])" } | Sort-Object -Unique)
        if ($newKeys.Count -ne 1) { throw 'Could not obtain one unambiguous ED25519 host key. Check the server address, port 22 and network. No host records were changed.' }
        [IO.File]::WriteAllText($newKeyFile, $newKeys[0] + "`n", [Text.UTF8Encoding]::new($false))
        $fingerprint = @(Invoke-SshKeyTool 'ssh-keygen.exe' @('-l', '-f', $newKeyFile, '-E', 'sha256'))
        $records = @()
        if (Test-Path -LiteralPath $KnownHosts) {
            $records = @(Invoke-SshKeyTool 'ssh-keygen.exe' @('-F', $Server, '-f', $KnownHosts) @(0, 1) |
                Where-Object { $_.Trim() -and -not $_.StartsWith('#') })
        }
        if (@($records | Where-Object { $_ -match '^@' }).Count) {
            throw 'This host has a revoked-key or certificate-authority record. Review known_hosts manually; it was not changed.'
        }
        $key = ($newKeys[0] -split '\s+')[2]
        if (@($records | Where-Object { $_ -match ('^\S+\s+ssh-ed25519\s+' + [regex]::Escape($key) + '(?:\s|$)') }).Count) {
            return
        }
        $otherRecords = @(foreach ($record in $records) {
            $aliases = ($record -split '\s+')[0] -split ','
            if (@($aliases | Where-Object { $_ -match '[*?!]' }).Count) {
                throw 'This host uses a known_hosts pattern. Review that entry manually; it was not changed.'
            }
            $remaining = @($aliases | Where-Object { $_ -ine $Server -and -not $_.StartsWith('|') })
            if ($remaining.Count) { $record -replace '^\S+', ($remaining -join ',') }
        })
        Write-Host "SSH server: ${Server}:22"
        if ($records.Count) {
            [IO.File]::WriteAllLines($oldKeyFile, [string[]]$records, [Text.UTF8Encoding]::new($false))
            Write-Host 'Previously saved host key(s):'
            Invoke-SshKeyTool 'ssh-keygen.exe' @('-l', '-f', $oldKeyFile, '-E', 'sha256') | ForEach-Object { Write-Host $_ }
            Write-Host 'Current ED25519 host key:'
            $fingerprint | ForEach-Object { Write-Host $_ }
            Write-Host 'The saved key differs. Verify this fingerprint in the server console, especially after reinstalling Ubuntu.'
            $answer = Read-Host 'Type YES to replace this server record and continue (Enter cancels)'
            if ($answer -cne 'YES') { throw 'Host-key update cancelled. Existing host records were not changed.' }
            Invoke-SshKeyTool 'ssh-keygen.exe' @('-R', $Server, '-f', $KnownHosts) | Out-Null
            Write-Host "Previous records backed up to ${KnownHosts}.old"
        } else {
            $fingerprint | ForEach-Object { Write-Host $_ }
            Write-Host 'First connection: saving this server host key.'
            [IO.Directory]::CreateDirectory((Split-Path -Parent $KnownHosts)) | Out-Null
        }
        $replacement = (@($otherRecords) + $newKeys[0]) -join "`n"
        [IO.File]::AppendAllText($KnownHosts, "`n" + $replacement + "`n", [Text.UTF8Encoding]::new($false))
    } finally {
        foreach ($file in @($newKeyFile, $oldKeyFile)) {
            if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
        }
    }
}

function Invoke-OpenSsh([string]$Program, [string[]]$ToolArguments,
    [string]$FailureMessage = 'SSH failed. Check the host-key warning, SSH credentials and network access.') {
    $saved = @{}
    foreach ($name in @('SSH_ASKPASS', 'SSH_ASKPASS_REQUIRE', 'DAFEIYU_CREDENTIAL_FILE')) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        $env:SSH_ASKPASS = $taskAskPass
        $env:SSH_ASKPASS_REQUIRE = 'force'
        $env:DAFEIYU_CREDENTIAL_FILE = $taskKeyPath
        & $Program @ToolArguments
        if ($LASTEXITCODE -ne 0) { throw ($FailureMessage + " Exit code: $LASTEXITCODE") }
    } finally {
        foreach ($name in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process')
        }
    }
}

function Sync-ServerTokens {
    Invoke-OpenSsh 'scp.exe' ($taskCommon + @('-P', '22', "${taskTarget}:dafeiyu/key.txt", $taskDownloaded))
    $remoteKeys = Read-KeyFile $taskDownloaded
    if ($remoteKeys['SSH_HOST'] -ne $taskAddress -or -not $remoteKeys['NAPCAT_TOKEN'] -or -not $remoteKeys['ONEBOT_TOKEN']) {
        throw 'Downloaded key.txt does not match this server, or the NapCat/OneBot tokens are missing.'
    }
    $updates = @{'NAPCAT_TOKEN' = $remoteKeys['NAPCAT_TOKEN']; 'ONEBOT_TOKEN' = $remoteKeys['ONEBOT_TOKEN']}
    $lines = [Collections.Generic.List[string]]::new()
    foreach ($line in [IO.File]::ReadAllLines($taskKeyPath, [Text.Encoding]::UTF8)) {
        if ($line.Trim() -match '^(AstrBot|NapCat):\s*https?://\S+$') { continue }
        $name = $line.Split([char[]]'=', 2, [StringSplitOptions]::None)[0].Trim()
        if (-not $line.TrimStart().StartsWith('#') -and $updates.ContainsKey($name)) {
            $lines.Add("${name}=$($updates[$name])")
            $updates.Remove($name)
        } else { $lines.Add($line) }
    }
    foreach ($name in @($updates.Keys)) { $lines.Add("${name}=$($updates[$name])") }
    while ($lines.Count -and -not $lines[$lines.Count - 1].Trim()) { $lines.RemoveAt($lines.Count - 1) }
    $lines.Add('')
    $lines.Add("AstrBot: http://${taskAddress}:6185/")
    $lines.Add("NapCat:  http://${taskAddress}:6099/webui/web_login")
    [IO.File]::WriteAllLines($taskKeyPath, $lines, [Text.UTF8Encoding]::new($false))
    Write-Host 'NapCat/OneBot tokens and service URLs saved to local key.txt. Other local fields were preserved.'
}

$taskKeyPath = Join-Path $taskRoot 'key.txt'
if (-not (Test-Path -LiteralPath $taskKeyPath)) {
    throw 'Copy key.example.txt to key.txt and fill it first.'
}
$taskKeys = Read-KeyFile $taskKeyPath
$taskAddress = $taskKeys['SSH_HOST']
$taskUser = $taskKeys['SSH_USER']
if ($taskAddress -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]*$' -or
    $taskUser -notmatch '^[a-z_][a-z0-9_-]*[$]?$') {
    throw 'Invalid SSH_HOST or SSH_USER in key.txt.'
}
if (-not $taskKeys['SSH_PASSWORD']) { throw 'Fill SSH_PASSWORD in key.txt.' }
$taskTarget = "${taskUser}@${taskAddress}"
$taskTemp = Join-Path ([IO.Path]::GetTempPath()) ('dafeiyu-upload-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $taskTemp | Out-Null
$taskArchive = Join-Path $taskTemp 'upload.tar.gz'
$taskAskPass = Join-Path $taskTemp 'ssh-askpass.exe'
$taskDownloaded = Join-Path $taskTemp 'server-key.txt'
$taskKeepArchive = $false

try {

    if (-not $SyncKey -and -not $Connect) {
        if (-not (Get-Command tar.exe -ErrorAction SilentlyContinue)) {
            throw 'Windows 10/11 tar.exe is required. Update Windows and try again.'
        }
        $taskFiles = @('key.txt', 'deploy.sh', 'configure.py', 'compose.yaml', 'pack')
        foreach ($taskFile in $taskFiles) {
            if (-not (Test-Path -LiteralPath (Join-Path $taskRoot $taskFile))) {
                throw "Missing deployment file: $taskFile"
            }
        }
        & tar.exe -czf $taskArchive --exclude=__pycache__ '--exclude=*.pyc' -C $taskRoot @taskFiles
        if ($LASTEXITCODE -ne 0) { throw 'Could not create upload archive.' }
        if ($PrepareOnly) {
            $taskKeepArchive = $true
            Write-Host 'Temporary archive prepared; remove it after transfer. It contains credentials.'
            Write-Output $taskArchive
            return
        }
    }

    foreach ($taskProgram in @('ssh.exe', 'scp.exe', 'ssh-keygen.exe')) {
        if (-not (Get-Command $taskProgram -ErrorAction SilentlyContinue)) {
            throw 'Enable the complete Windows OpenSSH Client optional feature first. See README.md.'
        }
    }
    $taskKnownHosts = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.ssh\known_hosts'
    Confirm-SshHostKey $taskAddress $taskKnownHosts $taskTemp $taskUser
    $taskAskPassSource = Join-Path $taskRoot 'ssh-askpass.cs'
    if (-not (Test-Path -LiteralPath $taskAskPassSource)) { throw 'Missing ssh-askpass.cs.' }
    if (-not (Test-Path -LiteralPath $taskAskPass) -or
        (Get-Item -LiteralPath $taskAskPassSource).LastWriteTimeUtc -gt (Get-Item -LiteralPath $taskAskPass).LastWriteTimeUtc) {
        $taskCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
        if (-not (Test-Path -LiteralPath $taskCompiler)) {
            $taskCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
        }
        if (-not (Test-Path -LiteralPath $taskCompiler)) { throw 'Windows .NET Framework 4 compiler is unavailable.' }
        & $taskCompiler /nologo /target:winexe ("/out:$taskAskPass") $taskAskPassSource
        if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the SSH password helper.' }
    }
    $taskCommon = @('-o', 'StrictHostKeyChecking=yes',
        '-o', 'UserKnownHostsFile=~/.ssh/known_hosts', '-o', 'HostKeyAlgorithms=ssh-ed25519',
        '-o', 'UpdateHostKeys=no', '-o', 'ControlPath=none', '-o', "HostKeyAlias=$taskAddress",
        '-o', 'ConnectTimeout=20', '-o', 'ServerAliveInterval=20',
        '-o', 'PreferredAuthentications=password', '-o', 'PubkeyAuthentication=no',
        '-o', 'PasswordAuthentication=yes', '-o', 'NumberOfPasswordPrompts=1')
    Write-Host 'Reading SSH credentials from key.txt automatically.'
    if ($Connect) {
        Invoke-OpenSsh 'ssh.exe' ($taskCommon + @('-p', '22', $taskTarget))
        return
    }

    if ($SyncKey) {
        Sync-ServerTokens
        return
    }

    try {
        Invoke-OpenSsh 'scp.exe' ($taskCommon + @('-P', '22', $taskArchive, "${taskTarget}:dafeiyu-upload.tar.gz"))
        $taskRemoteCommand = 'set -eu; umask 077; mkdir -p "$HOME/dafeiyu"; tar -xzf "$HOME/dafeiyu-upload.tar.gz" -C "$HOME/dafeiyu"; chmod 600 "$HOME/dafeiyu/key.txt"; rm -f "$HOME/dafeiyu-upload.tar.gz"'
        Invoke-OpenSsh 'ssh.exe' ($taskCommon + @('-p', '22', $taskTarget, $taskRemoteCommand))
        Write-Host 'Upload complete. Starting deployment on Ubuntu; keep this window open.'
        $taskDeployCommand = 'cd "$HOME/dafeiyu" && if [ "$(id -u)" -eq 0 ]; then bash deploy.sh; else sudo -n bash deploy.sh; fi'
        Invoke-OpenSsh 'ssh.exe' ($taskCommon + @('-p', '22', $taskTarget, $taskDeployCommand)) 'Ubuntu deployment failed. Fix the error above, then rerun upload.ps1.'
        Sync-ServerTokens
    } finally {
        if (Test-Path -LiteralPath $taskArchive) { Remove-Item -LiteralPath $taskArchive }
    }
    Write-Host 'Deployment complete. QQ login is left to you in NapCat WebUI.'
    Write-Host "AstrBot: http://${taskAddress}:6185/"
    Write-Host "NapCat:  http://${taskAddress}:6099/webui/web_login"
} finally {
    foreach ($taskFile in @($taskAskPass, $taskDownloaded, $taskArchive)) {
        if ($taskKeepArchive -and $taskFile -eq $taskArchive) { continue }
        if (Test-Path -LiteralPath $taskFile -PathType Leaf) { Remove-Item -LiteralPath $taskFile }
    }
    if ((Get-ChildItem -LiteralPath $taskTemp -Force | Measure-Object).Count -eq 0) {
        Remove-Item -LiteralPath $taskTemp
    }
}
