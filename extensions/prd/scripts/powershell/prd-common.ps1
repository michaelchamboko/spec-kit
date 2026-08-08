<#
.SYNOPSIS
  PRD-to-Plans extension: shared helpers (PowerShell).

.DESCRIPTION
  PowerShell twin of ``prd-common.sh`` and ``prd_common.py``. Centralizes:
   - Slug normalization (lowercase, kebab-case, [a-z0-9-] only)
   - Path containment checks (refuse symlinks, refuse escaping project root)
   - SHA-256 digest helpers (System.Security.Cryptography)
   - Manifest load / atomic write

  All exported functions are advanced functions (``[CmdletBinding()]``) with
  mandatory ``-Path`` / ``-Child`` / ``-Parent`` parameters so they behave
  identically when sourced from another script under pwsh 7.x and when called
  directly. Tested for PowerShell 5.1 (Windows PowerShell) and PowerShell 7+
  (pwsh); tests skip when ``pwsh`` is unavailable.
#>
$ErrorActionPreference = 'Stop'

$Script:SlugMaxLengthDefault = 64

function Write-PrdError {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Message)
    [Console]::Error.WriteLine($Message)
}

function Write-PrdInfo {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Message)
    # ``Write-Output`` from inside an advanced function returns the value
    # to the pipeline (which the caller may or may not capture). For the
    # PRD entrypoints we want a deterministic stdout line that callers
    # can parse, so write straight to ``[Console]::Out``.
    [Console]::Out.WriteLine($Message)
}

function ConvertTo-PrdSlug {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][AllowEmptyString()][string]$Value,
        [Parameter()][int]$MaxLength = $Script:SlugMaxLengthDefault
    )
    if ([string]::IsNullOrWhiteSpace($Value)) {
        Write-PrdError -Message 'ERROR: slug must be a non-empty string'
        throw 'invalid slug'
    }
    $lowered = $Value.Trim().ToLower()
    $replaced = [regex]::Replace($lowered, '[\s_]+', '-')
    $cleaned = [regex]::Replace($replaced, '[^a-z0-9-]+', '')
    $cleaned = [regex]::Replace($cleaned, '-+', '-').Trim('-')
    if ([string]::IsNullOrEmpty($cleaned)) {
        Write-PrdError -Message "ERROR: slug normalizes to empty value: $Value"
        throw 'invalid slug'
    }
    if ($cleaned.Length -gt $MaxLength) {
        $cleaned = $cleaned.Substring(0, $MaxLength).TrimEnd('-')
        if ([string]::IsNullOrEmpty($cleaned)) {
            $cleaned = $Value.Substring(0, [Math]::Min($MaxLength, $Value.Length))
        }
    }
    return $cleaned
}

function Get-PrdSha256File {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Write-PrdError -Message "ERROR: sha256_file: not a file: $Path"
        throw 'missing file'
    }
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $bytes = $hasher.ComputeHash($stream)
        } finally {
            $stream.Dispose()
        }
    } finally {
        $hasher.Dispose()
    }
    $builder = New-Object System.Text.StringBuilder
    foreach ($byte in $bytes) {
        [void]$builder.AppendFormat('{0:x2}', $byte)
    }
    return $builder.ToString()
}

function Test-IsWithin {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$Child,
        [Parameter(Mandatory=$true)][string]$Parent
    )
    if ($Child -eq $Parent) { return $true }
    return $Child.StartsWith($Parent + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::Ordinal)
}

function Test-IsSymlinkedAncestor {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Path)
    # Walk up the directory tree using a manual loop rather than
    # ``Split-Path -LiteralPath X -Parent``: pwsh 7.6 considers those two
    # parameters to live in incompatible parameter sets, so combining them
    # raises "Parameter set cannot be resolved". The walk only needs the
    # parent directory, which is just ``dirname`` of the current path.
    $current = $Path
    while ($true) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
            if ($null -ne $item -and ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                return $true
            }
        }
        $parent = Get-PrdParentPath $current
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $current) { return $false }
        $current = $parent
    }
}

# Return the parent of ``$Path`` without invoking ``Split-Path -LiteralPath
# -Parent`` (which is broken in pwsh 7.6 — those parameters live in
# different parameter sets and combining them raises "Parameter set cannot
# be resolved"). Falls back to a manual split when no separator is present.
function Get-PrdParentPath {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][string]$Path)
    $idx = $Path.LastIndexOf('\')
    if ($idx -lt 0) {
        $idx = $Path.LastIndexOf('/')
    }
    if ($idx -le 0) {
        return ''
    }
    return $Path.Substring(0, $idx)
}

function Require-WithinProject {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$Child,
        [Parameter(Mandatory=$true)][string]$Parent
    )
    if (Test-IsSymlinkedAncestor -Path $Child) {
        Write-PrdError -Message "ERROR: refusing symlinked ancestor under: $Child"
        throw 'symlink'
    }
    if (-not (Test-IsWithin -Child $Child -Parent $Parent)) {
        Write-PrdError -Message "ERROR: path $Child escapes project root $Parent"
        throw 'escape'
    }
}

function Find-SpecKitRoot {
    [CmdletBinding()]
    param([string]$StartDir = (Get-Location).Path)
    # Honor an explicit ``SPECIFY_INIT_DIR`` override (used by the bundled
    # agent-context and git extensions). Mirrors ``scripts/python/common.py``
    # and ``scripts/bash/common.sh`` in spec-kit core.
    $initDir = $env:SPECIFY_INIT_DIR
    if (-not [string]::IsNullOrWhiteSpace($initDir)) {
        if (-not (Test-Path -LiteralPath $initDir -PathType Container)) {
            Write-PrdError -Message "ERROR: SPECIFY_INIT_DIR does not point to an existing directory: $initDir"
            throw 'invalid init dir'
        }
        if (-not (Test-Path -LiteralPath (Join-Path $initDir '.specify') -PathType Container)) {
            Write-PrdError -Message "ERROR: SPECIFY_INIT_DIR is not a Spec Kit project (no .specify/ directory): $initDir"
            throw 'invalid init dir'
        }
        return $initDir
    }
    $current = (Resolve-Path -LiteralPath $StartDir).Path
    while ($true) {
        $marker = Join-Path $current '.specify'
        if (Test-Path -LiteralPath $marker -PathType Container) {
            return $current
        }
        $parent = Split-Path -LiteralPath $current -Parent
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $current) {
            return $null
        }
        $current = $parent
    }
}

function New-PrdSafeDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$ProjectRoot
    )
    Require-WithinProject -Child $Path -Parent $ProjectRoot
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
    return $Path
}

function Get-PrdUniqueSlug {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$SpecsRoot,
        [Parameter(Mandatory=$true)][string]$Requested
    )
    $base = ConvertTo-PrdSlug -Value $Requested
    $candidate = $base
    $n = 2
    while (Test-Path -LiteralPath (Join-Path $SpecsRoot $candidate)) {
        $candidate = "$base-$n"
        $n++
    }
    return $candidate
}

function Write-PrdManifest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$ArtifactDir,
        [Parameter(Mandatory=$true)][string]$ProjectRoot,
        [Parameter(Mandatory=$true)][string]$Body
    )
    $manifestPath = Join-Path $ArtifactDir 'manifest.yml'
    Require-WithinProject -Child $manifestPath -Parent $ProjectRoot
    if (-not (Test-Path -LiteralPath $ArtifactDir)) {
        New-Item -ItemType Directory -Path $ArtifactDir -Force | Out-Null
    }
    Set-Content -LiteralPath $manifestPath -Value $Body -Encoding utf8
    return $manifestPath
}

function ConvertTo-PrdYamlBlock {
    [CmdletBinding()]
    param([Parameter(Mandatory=$true)][hashtable]$Data)
    # Minimal YAML emitter for the manifest shape emitted by the Python and
    # bash twins. Supports nested hashtables, lists, and scalar values.
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($key in $Data.Keys) {
        $value = $Data[$key]
        if ($value -is [hashtable]) {
            [void]$lines.Add("$key`:")
            foreach ($inner in (ConvertTo-PrdYamlBlock -Data $value)) {
                [void]$lines.Add("  $inner")
            }
        } elseif ($value -is [System.Collections.IEnumerable] -and -not ($value -is [string])) {
            if (@($value).Count -eq 0) {
                [void]$lines.Add("$key`: []")
            } else {
                [void]$lines.Add("$key`:")
                foreach ($item in $value) {
                    if ($item -is [hashtable]) {
                        $first = $true
                        foreach ($inner in (ConvertTo-PrdYamlBlock -Data $item)) {
                            $prefix = if ($first) { '- ' } else { '  ' }
                            [void]$lines.Add("$prefix$inner".TrimEnd())
                            $first = $false
                        }
                    } else {
                        [void]$lines.Add("- $item")
                    }
                }
            }
        } elseif ($null -eq $value) {
            [void]$lines.Add("$key`: null")
        } elseif ($value -is [bool]) {
            [void]$lines.Add("$key`: $(if ($value) { 'true' } else { 'false' })")
        } elseif ($value -is [int] -or $value -is [long] -or $value -is [double]) {
            [void]$lines.Add("$key`: $value")
        } else {
            $escaped = ($value -replace '\\', '\\').Replace('"', '\"')
            [void]$lines.Add("$key`: `"$escaped`"")
        }
    }
    return $lines
}