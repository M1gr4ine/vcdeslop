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

# Antigravity invocation numbers are zero-based; missing fields retain compatibility.
$InvocationProp = $Payload.PSObject.Properties["invocationNum"]
if ($Harness -eq "antigravity" -and $null -ne $InvocationProp) {
    $Value = $InvocationProp.Value
    if (($Value -isnot [int] -and $Value -isnot [long]) -or $Value -ne 0) {
        Write-Output "{}"
        exit 0
    }
}

$ReviewPattern = '(?is)(?:(?<![A-Za-z0-9_])(?:code\s+review|pr\s+review|diff\s+review|(?:vc)?deslop)(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])(?:review|audit)(?![A-Za-z0-9_]).{0,40}?(?<![A-Za-z0-9_])(?:this|my|the|current|branch|pr|diff|commit|changes?|code|maintainability|project|repo|repository)(?![A-Za-z0-9_])|代码审查|审查代码|审一下|(?<![A-Za-z0-9_])review\s*一下|(?:看看|检查|审视).{0,16}(?:改动|代码|diff|提交|代码质量|质量|品位)|去\s*AI\s*味)'
if ([string]::IsNullOrEmpty($PromptText) -or ($PromptText -notmatch $ReviewPattern)) {
    Write-Output "{}"
    exit 0
}

$Reminder = "[vcdeslop auto-trigger] Review/cleanup intent detected: activate the ``vcdeslop`` skill and follow its protocol " +
    "(preflight scan if Python exists -> fresh same-model reviewer subagent -> verdict or surgical cleanup)."

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
