[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("antigravity", "claude", "codex", "dsh", "cursor")]
    [string]$Harness = "claude"
)

$ErrorActionPreference = "Stop"
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$RawInput = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($RawInput)) {
    Write-Output "{}"
    exit 0
}

$Payload = $null
try {
    $Payload = $RawInput | ConvertFrom-Json
} catch {
    Write-Output "{}"
    exit 0
}

$PromptText = ""
foreach ($Field in @("lastUserInput", "prompt", "user_prompt")) {
    $Prop = $Payload.PSObject.Properties[$Field]
    if ($null -ne $Prop -and $Prop.Value -is [string] -and $Prop.Value.Length -gt 0) {
        $PromptText = $Prop.Value
        break
    }
}

$ReviewPattern = '(?is)(?:(?<![A-Za-z0-9_])(?:code\s+review|pr\s+review|diff\s+review|(?:vc)?deslop)(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])(?:review|audit)(?![A-Za-z0-9_]).{0,40}?(?<![A-Za-z0-9_])(?:this|my|the|current|branch|pr|diff|commit|changes?|code|maintainability|project|repo|repository)(?![A-Za-z0-9_])|代码审查|审查代码|审一下|(?<![A-Za-z0-9_])review\s*一下|(?:看看|检查|审视).{0,16}(?:改动|代码|diff|提交|代码质量|质量|品位)|去\s*AI\s*味)'
if ([string]::IsNullOrEmpty($PromptText) -or ($PromptText -notmatch $ReviewPattern)) {
    Write-Output "{}"
    exit 0
}

$Reminder = "[vcdeslop auto-trigger] Review/cleanup intent detected. Follow the ``vcdeslop`` skill protocol:`n" +
    "1. If python/python3 is available, run ``python <vcdeslop_skill_dir>/scripts/preflight_scan.py --path . --mode diff``; otherwise inspect repo linters + raw git diff directly.`n" +
    "2. Delegate review to a fresh isolated (``spawn``, never ``fork``) subagent running on the EXACT SAME model (``inherit``, never ``haiku``/``flash``/``flash_lite``/``mini``/``Explore``).`n" +
    "3. Enforce the 4 Hard Redlines (Single-function Cognitive Complexity <= 15; Zero Boolean Traps & <= 4 positional params; Zero duplicate blocks; Zero circular or cross-layer/private imports; oversized files > 400 lines are advisory WARNING only) plus Cursor deslop, tombstone dead-code reaping, and mental mutation audit."

if ($Harness -eq "antigravity") {
    $Out = @{ injectSteps = @(@{ systemMessage = @{ systemMessage = $Reminder } }) }
} else {
    $EventName = "UserPromptSubmit"
    $EventProp = $Payload.PSObject.Properties["hook_event_name"]
    if ($null -ne $EventProp -and $EventProp.Value -is [string] -and $EventProp.Value.Length -gt 0) {
        $EventName = $EventProp.Value
    }
    $Out = @{ hookSpecificOutput = @{ hookEventName = $EventName; additionalContext = $Reminder } }
}

Write-Output ($Out | ConvertTo-Json -Compress -Depth 6)
