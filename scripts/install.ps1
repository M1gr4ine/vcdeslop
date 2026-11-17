<#
.SYNOPSIS
Native Windows PowerShell installer/uninstaller for vcdeslop across Claude Code, Codex, DSH, Antigravity, and Cursor.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("all", "claude", "codex", "dsh", "antigravity", "cursor", "universal")]
    [string[]]$Targets = @("all"),
    [string]$HomeDir = $HOME,
    [switch]$Copy,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$SkillSrc = Join-Path $RepoRoot "skills\vcdeslop"
$ClaudeAgentSrc = Join-Path $RepoRoot "adapters\claude\agents\vcdeslop-reviewer.md"
$AgyAgentSrc = Join-Path $RepoRoot "adapters\antigravity\agents\vcdeslop-reviewer.md"
$HookScript = Join-Path $RepoRoot "adapters\hooks\vcdeslop-hook.ps1"
$RuleSnippetPath = Join-Path $RepoRoot "adapters\rules\global-review-gate.md"
$Stamp = Get-Date -Format "yyyyMMddHHmmss"
$BackupRoot = Join-Path $HomeDir ".vcdeslop-backups\$Stamp"
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$Script:UniversalDone = $false

function Write-Log {
    param([string]$Message)
    [Console]::Out.WriteLine($Message)
}

function Resolve-TargetList {
    param([string[]]$InputTargets)
    if ($InputTargets -contains "all") {
        return @("universal", "claude", "codex", "dsh", "antigravity", "cursor")
    }
    return $InputTargets | Select-Object -Unique
}

function Test-PathSafe {
    param([string]$TargetPath)
    try {
        if (Test-Path -LiteralPath $TargetPath -ErrorAction Stop) { return "present" }
        return "absent"
    } catch {
        if ($DryRun -and ($_.Exception -is [System.UnauthorizedAccessException] -or $_.Exception.InnerException -is [System.UnauthorizedAccessException])) {
            Write-Log "[DRY-RUN] Sandbox blocks inspecting $TargetPath; would process in live run."
            return "blocked"
        }
        throw
    }
}

function Read-TextSafe {
    param([string]$FilePath)
    $Status = Test-PathSafe -TargetPath $FilePath
    if ($Status -eq "blocked") { return $null }
    if ($Status -eq "absent") { return "" }
    try {
        return [System.IO.File]::ReadAllText($FilePath, [System.Text.Encoding]::UTF8)
    } catch [System.UnauthorizedAccessException] {
        if ($DryRun) {
            Write-Log "[DRY-RUN] Sandbox blocks reading $FilePath; would update in live run."
            return $null
        }
        throw
    }
}

function Detach-ChildJunctions {
    param([string]$RootDir)
    Get-ChildItem -LiteralPath $RootDir -Recurse -Force -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Attributes -band [System.IO.FileAttributes]::ReparsePoint } |
        ForEach-Object { cmd /c rmdir "$($_.FullName)" | Out-Null }
}

function Remove-InstalledPath {
    param([string]$DestPath)
    if ((Test-PathSafe -TargetPath $DestPath) -ne "present") { return }
    if ($DryRun) { Write-Log "[DRY-RUN] Remove: $DestPath"; return }
    $Item = Get-Item -LiteralPath $DestPath -Force
    if ($Item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        if ($Item.PSIsContainer) { cmd /c rmdir "$DestPath" | Out-Null }
        else { Remove-Item -LiteralPath $DestPath -Force }
    } elseif ($Item.PSIsContainer) {
        Detach-ChildJunctions -RootDir $DestPath
        Remove-Item -LiteralPath $DestPath -Recurse -Force
    } else {
        Remove-Item -LiteralPath $DestPath -Force
    }
    Write-Log "Removed: $DestPath"
}

function Get-BackupDest {
    param([string]$OriginalPath)
    if ($OriginalPath.StartsWith($HomeDir, [System.StringComparison]::OrdinalIgnoreCase)) {
        $Rel = $OriginalPath.Substring($HomeDir.Length).TrimStart('\', '/')
    } else {
        $Rel = (Split-Path -Leaf (Split-Path -Parent $OriginalPath)) + "_" + (Split-Path -Leaf $OriginalPath)
    }
    $Tag = ($Rel -replace '^\.+', '' -replace '[\\/:\s\.]+', '_').Trim('_')
    $Dest = Join-Path $BackupRoot $Tag
    if (-not $DryRun) {
        New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
        if (Test-Path -LiteralPath $Dest) { Remove-InstalledPath -DestPath $Dest }
    }
    return $Dest
}

function Write-TextWithBackup {
    param([string]$FilePath, [string]$Content)
    if ($DryRun) { Write-Log "[DRY-RUN] Write: $FilePath"; return }
    New-Item -ItemType Directory -Path (Split-Path -Parent $FilePath) -Force | Out-Null
    if (Test-Path -LiteralPath $FilePath) {
        [System.IO.File]::Copy($FilePath, (Get-BackupDest -OriginalPath $FilePath), $true)
    }
    [System.IO.File]::WriteAllText($FilePath, $Content, $Utf8NoBom)
    Write-Log "Updated: $FilePath"
}

function Copy-CleanDirectory {
    param([string]$SourceDir, [string]$DestDir)
    $SkipDirs = @("__pycache__", ".git", ".pytest_cache")
    New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
    foreach ($Entry in (Get-ChildItem -LiteralPath $SourceDir -Force)) {
        if ($Entry.PSIsContainer) {
            if ($SkipDirs -notcontains $Entry.Name) {
                Copy-CleanDirectory -SourceDir $Entry.FullName -DestDir (Join-Path $DestDir $Entry.Name)
            }
        } elseif ($Entry.Extension -notin @(".pyc", ".pyo")) {
            Copy-Item -LiteralPath $Entry.FullName -Destination (Join-Path $DestDir $Entry.Name) -Force
        }
    }
}

function Backup-ExistingTarget {
    param([string]$DestPath)
    $Status = Test-PathSafe -TargetPath $DestPath
    if ($Status -eq "blocked") { return $false }
    if ($Status -ne "present") { return $true }
    if (-not $Force) {
        Write-Warning "Skipping existing target (re-run with -Force to back up and replace): $DestPath"
        return $false
    }
    $BackupPath = Get-BackupDest -OriginalPath $DestPath
    if ($DryRun) { Write-Log "[DRY-RUN] Backup: $DestPath -> $BackupPath"; return $true }
    Move-Item -LiteralPath $DestPath -Destination $BackupPath -Force
    Write-Log "Backed up: $DestPath -> $BackupPath"
    return $true
}

function Install-DirectoryEntry {
    param([string]$SourcePath, [string]$DestPath)
    if ($Copy) {
        Copy-CleanDirectory -SourceDir $SourcePath -DestDir $DestPath
        Write-Log "Installed (copy): $DestPath -> $SourcePath"
        return
    }
    try {
        New-Item -ItemType Junction -Path $DestPath -Target $SourcePath -ErrorAction Stop | Out-Null
        Write-Log "Installed (junction): $DestPath -> $SourcePath"
    } catch {
        Copy-CleanDirectory -SourceDir $SourcePath -DestDir $DestPath
        Write-Log "Installed (copy fallback): $DestPath -> $SourcePath"
    }
}

function Sync-Entry {
    param([string]$SourcePath, [string]$DestPath)
    if ($Uninstall) { Remove-InstalledPath -DestPath $DestPath; return }
    if (-not (Backup-ExistingTarget -DestPath $DestPath)) { return }
    $IsDir = (Get-Item -LiteralPath $SourcePath).PSIsContainer
    if ($DryRun) {
        $Label = if ($IsDir -and -not $Copy) { "junction" } else { "copy" }
        Write-Log "[DRY-RUN] $Label`: $DestPath -> $SourcePath"
        return
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $DestPath) -Force | Out-Null
    if (-not $IsDir) {
        Copy-Item -LiteralPath $SourcePath -Destination $DestPath -Force
        Write-Log "Installed (file): $DestPath -> $SourcePath"
        return
    }
    Install-DirectoryEntry -SourcePath $SourcePath -DestPath $DestPath
}

function ConvertTo-OrderedMap {
    param($Node)
    if ($Node -is [System.Management.Automation.PSCustomObject]) {
        $Map = [ordered]@{}
        foreach ($Prop in $Node.PSObject.Properties) { $Map[$Prop.Name] = ConvertTo-OrderedMap $Prop.Value }
        return $Map
    }
    if ($Node -is [System.Array]) {
        $List = [System.Collections.ArrayList]::new()
        foreach ($El in $Node) { [void]$List.Add((ConvertTo-OrderedMap $El)) }
        return ,$List.ToArray()
    }
    return $Node
}

function Remove-DeslopHookEntries {
    param($Entries)
    return @($Entries | Where-Object {
        if ($_ -isnot [System.Collections.IDictionary]) { return $true }
        $Inner = $_["hooks"]
        if ($null -eq $Inner) { return $true }
        -not (@($Inner) | Where-Object { $_ -is [System.Collections.IDictionary] -and $_["command"] -match "vcdeslop-hook" })
    })
}

function Update-HookJson {
    param([string]$Harness, [string]$ConfigDir)
    $FileMap = @{ claude = "settings.json"; codex = "hooks.json"; dsh = "hooks.json"; cursor = "hooks.json" }
    $HookPath = Join-Path $ConfigDir $FileMap[$Harness]
    $Raw = Read-TextSafe -FilePath $HookPath
    if ($null -eq $Raw -or ($Uninstall -and [string]::IsNullOrWhiteSpace($Raw))) { return }
    $Data = if ([string]::IsNullOrWhiteSpace($Raw)) { [ordered]@{} } else { ConvertTo-OrderedMap ($Raw | ConvertFrom-Json) }
    if (-not $Data.Contains("hooks") -or $null -eq $Data["hooks"]) { $Data["hooks"] = [ordered]@{} }
    $HooksMap = $Data["hooks"]
    $Existing = if ($HooksMap.Contains("UserPromptSubmit") -and $null -ne $HooksMap["UserPromptSubmit"]) { @($HooksMap["UserPromptSubmit"]) } else { @() }
    $Filtered = Remove-DeslopHookEntries -Entries $Existing
    if (-not $Uninstall) {
        $Cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$HookScript`" $Harness"
        $Filtered += [ordered]@{ matcher = ""; hooks = @([ordered]@{ type = "command"; command = $Cmd; timeout = 5 }) }
    }
    if ($Filtered.Count -gt 0) { $HooksMap["UserPromptSubmit"] = @($Filtered) } else { $HooksMap.Remove("UserPromptSubmit") }
    if ($HooksMap.Count -eq 0) { $Data.Remove("hooks") }
    Write-TextWithBackup -FilePath $HookPath -Content (($Data | ConvertTo-Json -Depth 10) + "`n")
}

function Remove-RuleGateBlock {
    param([string]$Harness, [string]$RulePath, [string]$Raw, [string]$MarkerRx)
    if ([string]::IsNullOrWhiteSpace($Raw)) { return }
    $Cleaned = ([regex]::Replace($Raw, $MarkerRx, "`n")).Trim()
    if ($Harness -eq "cursor" -and $Cleaned -replace '---', '' -match '^\s*description:') { $Cleaned = "" }
    if ([string]::IsNullOrEmpty($Cleaned)) { Remove-InstalledPath -DestPath $RulePath; return }
    Write-TextWithBackup -FilePath $RulePath -Content ($Cleaned + "`n")
}

function Update-RuleGate {
    param([string]$Harness, [string]$ConfigDir)
    $RuleMap = @{ claude = "CLAUDE.md"; codex = "AGENTS.md"; dsh = "AGENTS.md"; cursor = "rules\vcdeslop-gate.mdc" }
    $RulePath = Join-Path $ConfigDir $RuleMap[$Harness]
    $Raw = Read-TextSafe -FilePath $RulePath
    if ($null -eq $Raw) { return }
    $MarkerRx = '(?s)\r?\n?<!-- VCDESLOP-AUTO-GATE:START -->.*?<!-- VCDESLOP-AUTO-GATE:END -->\r?\n?'
    if ($Uninstall) { Remove-RuleGateBlock -Harness $Harness -RulePath $RulePath -Raw $Raw -MarkerRx $MarkerRx; return }
    $Snippet = ([System.IO.File]::ReadAllText($RuleSnippetPath, [System.Text.Encoding]::UTF8)).Trim()
    $Block = "<!-- VCDESLOP-AUTO-GATE:START -->`n$Snippet`n<!-- VCDESLOP-AUTO-GATE:END -->`n"
    if ($Raw -match '<!-- VCDESLOP-AUTO-GATE:START -->') {
        $Updated = ([regex]::Replace($Raw, $MarkerRx, "`n$Block")).TrimStart("`r", "`n")
    } elseif ($Harness -eq "cursor" -and [string]::IsNullOrWhiteSpace($Raw)) {
        $Updated = "---`ndescription: VCDeslop code taste & review gate`nalwaysApply: true`n---`n`n$Block"
    } else {
        $Updated = if ([string]::IsNullOrWhiteSpace($Raw)) { $Block } else { $Raw.TrimEnd() + "`n`n" + $Block }
    }
    Write-TextWithBackup -FilePath $RulePath -Content $Updated
}

function Sync-AntigravityPlugin {
    $AgyDest = Join-Path $HomeDir ".gemini\config\plugins\vcdeslop"
    if ($Uninstall) { Remove-InstalledPath -DestPath $AgyDest; return }
    if (-not (Backup-ExistingTarget -DestPath $AgyDest)) { return }
    $Pairs = @(
        @("plugin.json", "plugin.json"),
        @("hooks.json", "hooks.json"),
        @("rules\AGENTS.md", "rules\AGENTS.md"),
        @("adapters\antigravity\agents\vcdeslop-reviewer.md", "agents\vcdeslop-reviewer.md"),
        @("adapters\hooks\vcdeslop-hook", "adapters\hooks\vcdeslop-hook"),
        @("adapters\hooks\vcdeslop-hook.cmd", "adapters\hooks\vcdeslop-hook.cmd"),
        @("adapters\hooks\vcdeslop-hook.ps1", "adapters\hooks\vcdeslop-hook.ps1")
    )
    foreach ($Pair in $Pairs) {
        Sync-Entry -SourcePath (Join-Path $RepoRoot $Pair[0]) -DestPath (Join-Path $AgyDest $Pair[1])
    }
    Sync-Entry -SourcePath $SkillSrc -DestPath (Join-Path $AgyDest "skills\vcdeslop")
}

foreach ($Target in (Resolve-TargetList -InputTargets $Targets)) {
    if ($Target -in @("universal", "codex", "dsh", "cursor") -and -not $Script:UniversalDone) {
        if ($Target -eq "universal" -or -not $Uninstall) {
            $Script:UniversalDone = $true
            Sync-Entry -SourcePath $SkillSrc -DestPath (Join-Path $HomeDir ".agents\skills\vcdeslop")
        }
    }
    switch ($Target) {
        "claude" {
            $Dir = Join-Path $HomeDir ".claude"
            Sync-Entry -SourcePath $SkillSrc -DestPath (Join-Path $Dir "skills\vcdeslop")
            Sync-Entry -SourcePath $ClaudeAgentSrc -DestPath (Join-Path $Dir "agents\vcdeslop-reviewer.md")
            Update-HookJson -Harness "claude" -ConfigDir $Dir
            Update-RuleGate -Harness "claude" -ConfigDir $Dir
        }
        { $_ -in @("codex", "dsh", "cursor") } {
            $Dir = Join-Path $HomeDir ".$Target"
            Sync-Entry -SourcePath $SkillSrc -DestPath (Join-Path $Dir "skills\vcdeslop")
            Update-HookJson -Harness $Target -ConfigDir $Dir
            Update-RuleGate -Harness $Target -ConfigDir $Dir
        }
        "antigravity" { Sync-AntigravityPlugin }
    }
}
Write-Log "Completed ($($Targets -join ', '))."
