# 仅读取 NTFS 权限元数据，不读取或打印凭据内容。
$auditProject = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$auditTargets = @($auditProject, (Join-Path $auditProject '.env'), (Join-Path $auditProject 'data'))
$auditRows = foreach ($auditTarget in $auditTargets) {
    $auditAcl = Get-Acl -LiteralPath $auditTarget
    [pscustomobject]@{
        Path = $auditTarget
        Owner = $auditAcl.Owner
        Rules = @($auditAcl.Access | ForEach-Object {
            [pscustomobject]@{Identity = $_.IdentityReference.Value; Rights = $_.FileSystemRights.ToString(); Type = $_.AccessControlType.ToString(); Inherited = $_.IsInherited}
        })
    }
}
$auditRows | ConvertTo-Json -Depth 5
