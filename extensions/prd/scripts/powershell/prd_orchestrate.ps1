<#
.SYNOPSIS
  PRD-to-Plans: PowerShell entrypoint for ``speckit.prd.orchestrate``.

.DESCRIPTION
  Pure dispatch wrapper around the canonical Python state engine
  (``prd_orchestrate.py``). Forwards every argument verbatim and
  emits the engine's single-line JSON on stdout. Exits with the
  engine's status code.

.PARAMETER Action
  One of ``initialize``, ``status``, ``next``, ``start``,
  ``evidence``, ``complete``, ``block``, ``reopen``, ``approve``.

.EXAMPLE
  pwsh -File prd_orchestrate.ps1 -Slug demo -Action status
.EXAMPLE
  pwsh -File prd_orchestrate.ps1 -Slug demo -Action start -Task SLC-001-T001 -Owner alice
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Slug,
    [Parameter(Mandatory=$true)][ValidateSet('initialize','status','next','start','evidence','complete','block','reopen','approve')][string]$Action,
    [string]$Task,
    [string]$Owner,
    [string]$Check,
    [string]$Result,
    [string]$Path,
    [string]$Reason,
    [string]$Stage,
    [string]$ApprovedBy
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Locate the Python interpreter. Prefer $env:SPECKIT_PYTHON; fall
# back to ``python`` or ``python3`` on PATH.
$python = $env:SPECKIT_PYTHON
if ([string]::IsNullOrWhiteSpace($python)) {
    $candidates = @('python', 'python3')
    foreach ($candidate in $candidates) {
        $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($null -ne $resolved) {
            $python = $candidate
            break
        }
    }
}
if ([string]::IsNullOrWhiteSpace($python)) {
    [Console]::Error.WriteLine('ERROR: no Python interpreter on PATH (set $env:SPECKIT_PYTHON or install python/python3)')
    exit 1
}

# Build the argument vector in a deterministic order so JSON output
# is byte-identical to the bash twin.
$argumentList = @('-' + $Slug, '-Action', $Action)
if ($PSBoundParameters.ContainsKey('Task')) { $argumentList += @('-Task', $Task) }
if ($PSBoundParameters.ContainsKey('Owner')) { $argumentList += @('-Owner', $Owner) }
if ($PSBoundParameters.ContainsKey('Check')) { $argumentList += @('-Check', $Check) }
if ($PSBoundParameters.ContainsKey('Result')) { $argumentList += @('-Result', $Result) }
if ($PSBoundParameters.ContainsKey('Path')) { $argumentList += @('-Path', $Path) }
if ($PSBoundParameters.ContainsKey('Reason')) { $argumentList += @('-Reason', $Reason) }
if ($PSBoundParameters.ContainsKey('Stage')) { $argumentList += @('-Stage', $Stage) }
if ($PSBoundParameters.ContainsKey('ApprovedBy')) { $argumentList += @('-ApprovedBy', $ApprovedBy) }

# Translate the key/value pairs to the ``key=value`` form the
# Python engine expects.
$argsForPython = @('-Slug', $Slug)
# Always pass Action last for output parity; the engine parser
# tolerates any order.
$argsForPython = @()
if ($Slug) { $argsForPython += @("slug=$Slug") }
if ($Action) { $argsForPython += @("action=$Action") }
foreach ($pair in @(
    @{K='Task'; V=$Task},
    @{K='Owner'; V=$Owner},
    @{K='Check'; V=$Check},
    @{K='Result'; V=$Result},
    @{K='Path'; V=$Path},
    @{K='Reason'; V=$Reason},
    @{K='Stage'; V=$Stage},
    @{K='ApprovedBy'; V=$ApprovedBy}
)) {
    if (-not [string]::IsNullOrWhiteSpace($pair.V)) {
        $argsForPython += @("$($pair.K)=$($pair.V)")
    }
}

& $python (Join-Path $ScriptDir 'prd_orchestrate.py') @argsForPython
exit $LASTEXITCODE