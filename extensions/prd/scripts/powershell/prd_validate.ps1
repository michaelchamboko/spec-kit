<#
.SYNOPSIS
  PRD-to-Plans: PowerShell entrypoint for ``speckit.prd.validate``.

.DESCRIPTION
  Read-only structural, traceability, and state-consistency checks twin
  of ``prd_validate.py`` and ``prd_validate.sh``. Performs manifest
  schema validation, source integrity, requirements traceability, slice
  graph checks, and Council review presence. Never modifies artifacts.

.PARAMETER Slug
  The PRD slug; normalized to kebab-case.

.PARAMETER Phase
  Validation phase: ``decomposition`` (default ``AWAITING_DECOMPOSITION_APPROVAL``),
  ``final`` (``PLANNING`` or ``PLAN_READY``), or ``all`` (the union).

.EXAMPLE
  pwsh -File prd_validate.ps1 -Slug my-feature -Phase decomposition
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Slug,
    [ValidateSet('decomposition','final','all')][string]$Phase = 'all'
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'prd-common.ps1')

$ArtifactDirName = '000-spec-of-specs'

function Add-ValidationResult {
    [CmdletBinding()]
    param(
        [string]$Name,
        [bool]$Pass,
        [string]$Detail = ''
    )
    $Script:Results += [pscustomobject]@{
        Name = $Name
        Status = if ($Pass) { 'PASS' } else { 'FAIL' }
        Detail = $Detail
    }
}

function Get-ManifestValue {
    [CmdletBinding()]
    param(
        [string]$ManifestPath,
        [string]$Key
    )
    $line = Select-String -LiteralPath $ManifestPath -Pattern "^${Key}:" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $line) { return $null }
    $value = ($line.Line -replace "^${Key}:\s*", '').Trim().Trim('"')
    return $value
}

function Test-ManifestFields {
    [CmdletBinding()]
    param([string]$ManifestPath)
    foreach ($field in 'schema_version','extension','slug','state','active_version','source','slices') {
        $present = Select-String -LiteralPath $ManifestPath -Pattern "^${field}:" -ErrorAction SilentlyContinue
        if ($null -eq $present) {
            Add-ValidationResult -Name "manifest.$field" -Pass $false -Detail 'missing required field'
        } else {
            Add-ValidationResult -Name "manifest.$field" -Pass $true
        }
    }
}

function Test-SourceIntegrity {
    [CmdletBinding()]
    param(
        [string]$ProjectRoot,
        [string]$ManifestPath
    )
    $preservedRel = Get-ManifestValue -ManifestPath $ManifestPath -Key 'preserved_at'
    if ([string]::IsNullOrEmpty($preservedRel)) {
        Add-ValidationResult -Name 'source.preserved' -Pass $false -Detail 'missing preserved file'
        return
    }
    $full = Join-Path $ProjectRoot $preservedRel
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        Add-ValidationResult -Name 'source.preserved' -Pass $false -Detail "missing preserved file: $preservedRel"
        return
    }
    Add-ValidationResult -Name 'source.preserved' -Pass $true
    $expected = Get-ManifestValue -ManifestPath $ManifestPath -Key 'sha256'
    $actual = Get-PrdSha256File -Path $full
    if (-not [string]::IsNullOrEmpty($expected) -and $expected -ne $actual) {
        Add-ValidationResult -Name 'source.sha256' -Pass $false -Detail "expected=$expected got=$actual"
    } else {
        Add-ValidationResult -Name 'source.sha256' -Pass $true
    }
    $normalized = ($preservedRel -replace '\.[^.]+$', '') + '.normalized.md'
    $normalizedFull = Join-Path $ProjectRoot $normalized
    if (Test-Path -LiteralPath $normalizedFull -PathType Leaf) {
        Add-ValidationResult -Name 'source.normalized' -Pass $true
    } else {
        Add-ValidationResult -Name 'source.normalized' -Pass $false -Detail "missing $normalized"
    }
}

function Test-Requirements {
    [CmdletBinding()]
    param([string]$ArtifactDir)
    $req = Join-Path $ArtifactDir 'requirements.md'
    if (-not (Test-Path -LiteralPath $req -PathType Leaf)) {
        Add-ValidationResult -Name 'requirements.exists' -Pass $false -Detail 'requirements.md missing'
        return
    }
    Add-ValidationResult -Name 'requirements.exists' -Pass $true
    $ids = Select-String -LiteralPath $req -Pattern 'PRD-(FR|NFR)-[0-9]+' -AllMatches |
        ForEach-Object { $_.Matches[0].Value }
    if (-not $ids -or $ids.Count -eq 0) {
        Add-ValidationResult -Name 'requirements.ids' -Pass $false -Detail 'no PRD-FR-/PRD-NFR- ids'
        return
    }
    Add-ValidationResult -Name 'requirements.ids' -Pass $true
    if (($ids | Sort-Object -Unique).Count -ne $ids.Count) {
        Add-ValidationResult -Name 'requirements.unique' -Pass $false -Detail 'duplicate ids'
    } else {
        Add-ValidationResult -Name 'requirements.unique' -Pass $true
    }
}

function Test-Slices {
    [CmdletBinding()]
    param(
        [string]$ManifestPath,
        [string]$PrdDir
    )
    $sliceLines = Select-String -LiteralPath $ManifestPath -Pattern '^  - id:' |
        ForEach-Object { $_.Line -replace '^  - id:\s*', '' -replace '^"', '' -replace '"$', '' }
    if (-not $sliceLines -or $sliceLines.Count -eq 0) {
        Add-ValidationResult -Name 'slices.present' -Pass $false -Detail 'slices array empty'
        return
    }
    Add-ValidationResult -Name 'slices.present' -Pass $true -Detail "$($sliceLines.Count) slice(s)"
    foreach ($line in (Select-String -LiteralPath $ManifestPath -Pattern '^    directory:')) {
        $dir = ($line.Line -replace '^    directory:\s*', '').Trim().Trim('"')
        if (-not (Test-Path -LiteralPath (Join-Path $PrdDir $dir) -PathType Container)) {
            Add-ValidationResult -Name "slices.materialized[$dir]" -Pass $false -Detail 'slice directory missing'
        }
    }
}

function Test-CouncilReviews {
    [CmdletBinding()]
    param(
        [string]$ArtifactDir,
        [string]$Phase
    )
    $reviewsDir = Join-Path $ArtifactDir 'reviews'
    if ($Phase -eq 'decomposition' -or $Phase -eq 'all') {
        if (Test-Path -LiteralPath (Join-Path $reviewsDir 'decomposition-v001.md') -PathType Leaf) {
            Add-ValidationResult -Name 'reviews.decomposition' -Pass $true
        } else {
            Add-ValidationResult -Name 'reviews.decomposition' -Pass $false -Detail 'decomposition review missing'
        }
    }
    if ($Phase -eq 'final' -or $Phase -eq 'all') {
        if (Test-Path -LiteralPath (Join-Path $reviewsDir 'final-v001.md') -PathType Leaf) {
            Add-ValidationResult -Name 'reviews.final' -Pass $true
        } else {
            Add-ValidationResult -Name 'reviews.final' -Pass $false -Detail 'final review missing'
        }
    }
}

function Test-ChildArtifacts {
    [CmdletBinding()]
    param(
        [string]$PrdDir,
        [string]$ManifestPath
    )
    foreach ($line in (Select-String -LiteralPath $ManifestPath -Pattern '^    directory:')) {
        $dir = ($line.Line -replace '^    directory:\s*', '').Trim().Trim('"')
        foreach ($leaf in 'spec.md','plan.md','tasks.md','code-impact.md') {
            $target = Join-Path (Join-Path $PrdDir $dir) $leaf
            if (Test-Path -LiteralPath $target -PathType Leaf) {
                Add-ValidationResult -Name "artifacts.$dir/$leaf" -Pass $true
            } else {
                Add-ValidationResult -Name "artifacts.$dir/$leaf" -Pass $false -Detail 'missing'
            }
        }
    }
}

function Invoke-PrdValidate {
    [CmdletBinding()]
    param()
    $Script:Results = @()
    $projectRoot = Find-SpecKitRoot
    if ($null -eq $projectRoot) {
        Write-PrdError 'ERROR: not inside a Spec Kit project (.specify/ not found)'
        return 1
    }
    if ([string]::IsNullOrWhiteSpace($Slug)) {
        Write-PrdError 'ERROR: missing -Slug'
        return 2
    }
    try {
        $normalizedSlug = ConvertTo-PrdSlug -Value $Slug
    } catch {
        return 1
    }
    $specsRoot = New-PrdSafeDirectory -Path (Join-Path $projectRoot '.specify/specs') -ProjectRoot $projectRoot
    $prdDir = Join-Path $specsRoot $normalizedSlug
    $artifactDir = Join-Path $prdDir $ArtifactDirName
    $manifestPath = Join-Path $artifactDir 'manifest.yml'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        Write-PrdError "ERROR: manifest.yml not found at $artifactDir"
        return 1
    }
    $state = Get-ManifestValue -ManifestPath $manifestPath -Key 'state'
    if ($Phase -eq 'decomposition' -and $state -notin @('AWAITING_DECOMPOSITION_APPROVAL','PLANNING','PLAN_READY')) {
        Write-PrdError "ERROR: phase=decomposition requires state >= AWAITING_DECOMPOSITION_APPROVAL (got $state)"
        return 1
    }
    if ($Phase -eq 'final' -and $state -notin @('PLANNING','PLAN_READY')) {
        Write-PrdError "ERROR: phase=final requires state PLANNING|PLAN_READY (got $state)"
        return 1
    }

    Test-ManifestFields -ManifestPath $manifestPath
    Test-SourceIntegrity -ProjectRoot $projectRoot -ManifestPath $manifestPath
    Test-Requirements -ArtifactDir $artifactDir
    if ($state -in @('AWAITING_DECOMPOSITION_APPROVAL','PLANNING','PLAN_READY')) {
        Test-Slices -ManifestPath $manifestPath -PrdDir $prdDir
    }
    Test-CouncilReviews -ArtifactDir $artifactDir -Phase $Phase
    if ($state -in @('PLANNING','PLAN_READY') -or $Phase -eq 'final') {
        Test-ChildArtifacts -PrdDir $prdDir -ManifestPath $manifestPath
    }

    $passed = ($Script:Results | Where-Object { $_.Status -eq 'PASS' }).Count
    $failed = ($Script:Results | Where-Object { $_.Status -eq 'FAIL' }).Count
    $summary = [ordered]@{
        slug = $normalizedSlug
        phase = $Phase
        checks_passed = $passed
        checks_failed = $failed
        state = $state
        manifest = ".specify/specs/$normalizedSlug/$ArtifactDirName/manifest.yml"
        failures = @($Script:Results | Where-Object { $_.Status -eq 'FAIL' } | ForEach-Object { @{ name = $_.Name; detail = $_.Detail } })
    }
    Write-PrdInfo (ConvertTo-Json -InputObject $summary -Compress)
    return $(if ($failed -eq 0) { 0 } else { 1 })
}

exit (Invoke-PrdValidate)