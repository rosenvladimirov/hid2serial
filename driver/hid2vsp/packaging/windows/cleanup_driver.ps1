# Delete the hid2vsp driver package from the Windows driver store.
# pnputil /delete-driver wants the OEM-assigned name (oemNN.inf), not
# our INF — so we look it up via /enum-drivers, matching by Original Name.
$ErrorActionPreference = 'SilentlyContinue'
$blocks = (& pnputil /enum-drivers) -join "`n" -split "`n`n"
foreach ($b in $blocks) {
    if ($b -match 'Original Name:\s*hid2vsp\.inf' -and
        $b -match 'Published Name:\s*(\S+)') {
        & pnputil /delete-driver $Matches[1] /uninstall /force | Out-Null
    }
}
