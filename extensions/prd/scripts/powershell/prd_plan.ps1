<#
.SYNOPSIS
  PRD-to-Plans: PowerShell entrypoint for ``speckit.prd.plan``.

.DESCRIPTION
  Deterministic I/O twin of ``prd_plan.py`` and ``prd_plan.sh``. The
  command body drives the AI-assisted decomposition; this script
  materializes the workspace, preserves the source, and freezes the slice
  sequence on approval.

.PARAMETER Source
  Path or pasted content for the PRD source.

.PARAMETER Slug
  The PRD slug; normalized to kebab-case.

.PARAMETER Approve
  Switch: freeze the decomposition sequence and materialize slice directories.

.PARAMETER Finalize
  Switch: mark the workspace as ``PLAN_READY``.

.EXAMPLE
  pwsh -File prd_plan.ps1 -Source ./prd.md -Slug my-feature
.EXAMPLE
  pwsh -File prd_plan.ps1 -Slug my-feature -Approve
#>
param(
    [string]$Source,
    [string]$Slug,
    [switch]$Approve,
    [switch]$Finalize
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'prd-common.ps1')

$DefaultVersion = 'v001'
$ArtifactDirName = '000-spec-of-specs'

function Get-UtcNowIso {
    return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}

function Resolve-SourceBytes {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Value)
    if ($Value -eq '-') {
        return [Console]::In.ReadToEnd()
    }
    if (Test-Path -LiteralPath $Value -PathType Leaf) {
        return [System.IO.File]::ReadAllText($Value)
    }
    return $Value
}

function Get-NextSlicePrefix {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][int]$Count)
    return ('{0:000}' -f ($Count + 1))
}

function Build-IntakeBody {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$Slug,
        [Parameter(Mandatory=$true)][string]$PreservedRel,
        [Parameter(Mandatory=$true)][int]$ByteSize,
        [Parameter(Mandatory=$true)][string]$Sha256,
        [Parameter(Mandatory=$true)][string]$OriginalName,
        [Parameter(Mandatory=$true)][string]$Authority
    )
    $now = Get-UtcNowIso
    $body = [ordered]@{
        schema_version = '1.0'
        extension = 'prd'
        slug = $Slug
        state = 'AWAITING_DECOMPOSITION_APPROVAL'
        created_at = $now
        active_version = $DefaultVersion
        source = [ordered]@{
            authority = $Authority
            fetched_at = $now
            original_name = $OriginalName
            byte_size = $ByteSize
            sha256 = $Sha256
            preserved_at = $PreservedRel
        }
        slices = @()
        decomposition_version = $DefaultVersion
        frozen_sequence = $false
    }
    $lines = ConvertTo-PrdYamlBlock -Data $body
    return ($lines -join "`n")
}

function Build-ApproveBody {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Slug)
    $now = Get-UtcNowIso
    $body = [ordered]@{
        schema_version = '1.0'
        extension = 'prd'
        slug = $Slug
        state = 'PLANNING'
        frozen_sequence = $true
        decomposition_approval_version = $DefaultVersion
        slices = @()
    }
    $lines = ConvertTo-PrdYamlBlock -Data $body
    return ($lines -join "`n")
}

function Build-FinalizeBody {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Slug)
    $now = Get-UtcNowIso
    $body = [ordered]@{
        schema_version = '1.0'
        extension = 'prd'
        slug = $Slug
        state = 'PLAN_READY'
        final_review_version = $DefaultVersion
        finalized_at = $now
    }
    $lines = ConvertTo-PrdYamlBlock -Data $body
    return ($lines -join "`n")
}

function Invoke-PrdPlan {
    [CmdletBinding()]
    param()
    $projectRoot = Find-SpecKitRoot
    if ($null -eq $projectRoot) {
        Write-PrdError -Message 'ERROR: not inside a Spec Kit project (.specify/ not found)'
        return 1
    }
    $specsRoot = New-PrdSafeDirectory -Path (Join-Path $projectRoot '.specify/specs') -ProjectRoot $projectRoot
    $resolvedSlug = ''
    if (-not [string]::IsNullOrWhiteSpace($Slug)) {
        try {
            $resolvedSlug = ConvertTo-PrdSlug -Value $Slug
        } catch {
            return 1
        }
    }

    if ($Approve) {
        if ([string]::IsNullOrWhiteSpace($resolvedSlug)) {
            Write-PrdError -Message 'ERROR: -Approve requires -Slug'
            return 2
        }
        $prdDir = New-PrdSafeDirectory -Path (Join-Path $specsRoot $resolvedSlug) -ProjectRoot $projectRoot
        $artifactDir = New-PrdSafeDirectory -Path (Join-Path $prdDir $ArtifactDirName) -ProjectRoot $projectRoot
        $body = Build-ApproveBody -Slug $resolvedSlug
        Write-PrdManifest -ArtifactDir $artifactDir -ProjectRoot $projectRoot -Body $body | Out-Null
        Write-PrdInfo -Message "{`"status`":`"PLANNING`",`"slug`":`"$resolvedSlug`"}"
        return 0
    }

    if ($Finalize) {
        if ([string]::IsNullOrWhiteSpace($resolvedSlug)) {
            Write-PrdError -Message 'ERROR: -Finalize requires -Slug'
            return 2
        }
        $prdDir = New-PrdSafeDirectory -Path (Join-Path $specsRoot $resolvedSlug) -ProjectRoot $projectRoot
        $artifactDir = New-PrdSafeDirectory -Path (Join-Path $prdDir $ArtifactDirName) -ProjectRoot $projectRoot
        $body = Build-FinalizeBody -Slug $resolvedSlug
        Write-PrdManifest -ArtifactDir $artifactDir -ProjectRoot $projectRoot -Body $body | Out-Null
        Write-PrdInfo -Message "{`"status`":`"PLAN_READY`",`"slug`":`"$resolvedSlug`"}"
        return 0
    }

    if ([string]::IsNullOrWhiteSpace($Source)) {
        Write-PrdError -Message 'ERROR: intake mode requires -Source <path|pasted>'
        return 2
    }
    if ([string]::IsNullOrWhiteSpace($resolvedSlug)) {
        $resolvedSlug = Get-PrdUniqueSlug -SpecsRoot $specsRoot -Requested 'prd'
    } else {
        $resolvedSlug = Get-PrdUniqueSlug -SpecsRoot $specsRoot -Requested $resolvedSlug
    }
    $prdDir = New-PrdSafeDirectory -Path (Join-Path $specsRoot $resolvedSlug) -ProjectRoot $projectRoot
    $artifactDir = New-PrdSafeDirectory -Path (Join-Path $prdDir $ArtifactDirName) -ProjectRoot $projectRoot
    $sourceDir = New-PrdSafeDirectory -Path (Join-Path $artifactDir 'source') -ProjectRoot $projectRoot

    $extension = '.md'
    $authority = 'pasted'
    $originalName = 'pasted.md'
    if ($Source -ne '-' -and (Test-Path -LiteralPath $Source -PathType Leaf)) {
        $authority = 'file'
        $extension = [System.IO.Path]::GetExtension($Source)
        if ([string]::IsNullOrEmpty($extension)) { $extension = '.md' }
        $originalName = Split-Path -Leaf $Source
    }
    $leaf = "prd-$DefaultVersion$extension"
    $target = Join-Path $sourceDir $leaf
    Require-WithinProject -Child $target -Parent $projectRoot
    $bytes = Resolve-SourceBytes -Value $Source
    Set-Content -LiteralPath $target -Value $bytes -Encoding utf8
    $byteSize = (Get-Item -LiteralPath $target).Length
    $digest = Get-PrdSha256File -Path $target
    $preservedRel = $target.Substring($projectRoot.Length + 1) -replace '\\','/'

    $body = Build-IntakeBody -Slug $resolvedSlug -PreservedRel $preservedRel -ByteSize $byteSize -Sha256 $digest -OriginalName $originalName -Authority $authority
    Write-PrdManifest -ArtifactDir $artifactDir -ProjectRoot $projectRoot -Body $body | Out-Null
    Write-PrdInfo -Message "{`"status`":`"AWAITING_DECOMPOSITION_APPROVAL`",`"slug`":`"$resolvedSlug`",`"manifest`":`".specify/specs/$resolvedSlug/$ArtifactDirName/manifest.yml`",`"source_digest`":`"$digest`"}"
    return 0
}

exit (Invoke-PrdPlan)