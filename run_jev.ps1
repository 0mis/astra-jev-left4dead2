param([ValidateSet('smoke','pilot','general','evaluate')][string]$Mode='smoke', [int]$GamePid=0, [int]$Seconds=30, [switch]$Resume)
$ErrorActionPreference = 'Stop'
if (-not $env:TYPESAFE_API_KEY) { throw 'Set TYPESAFE_API_KEY in this process first; never put it in source control.' }
if (-not $env:L4D2_JEV_CAP_USD) { throw 'Set your own explicit L4D2_JEV_CAP_USD limit first.' }
$l4dPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($Mode -eq 'smoke') {
    & $l4dPython (Join-Path $PSScriptRoot 'jev_bridge.py')
} elseif ($Mode -eq 'evaluate') {
    & $l4dPython (Join-Path $PSScriptRoot 'evaluate_agent.py')
} else {
    if ($GamePid -le 0) { throw 'An explicitly verified game process ID is required.' }
    $l4dArguments = @('--pid', "$GamePid", '--seconds', "$Seconds")
    if ($Resume) { $l4dArguments += '--resume' }
    $l4dScript = if ($Mode -eq 'general') { 'agent_controller.py' } else { 'controller.py' }
    & $l4dPython (Join-Path $PSScriptRoot $l4dScript) @l4dArguments
}
if ($LASTEXITCODE -ne 0) { throw 'L4D2 Jev run stopped; inspect your private checkpoint.' }
