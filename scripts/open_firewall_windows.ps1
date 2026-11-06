# Allow the Pi to reach fdbench on this laptop (direct feed, remote mode). Run once as administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\open_firewall_windows.ps1
# Also allows UDP 8792: stage marks from her MCP servers (docs/STAGES.md).
param([int]$Port = 8791, [int]$MarksPort = 8792)
New-NetFirewallRule -DisplayName "fdbench $Port" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private,Domain | Out-Null
New-NetFirewallRule -DisplayName "fdbench marks $MarksPort" -Direction Inbound -Action Allow -Protocol UDP -LocalPort $MarksPort -Profile Private,Domain | Out-Null
Write-Host "Inbound TCP $Port and UDP $MarksPort allowed on private/domain networks. Remove with: Remove-NetFirewallRule -DisplayName 'fdbench $Port'; Remove-NetFirewallRule -DisplayName 'fdbench marks $MarksPort'"
