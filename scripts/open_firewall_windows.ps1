# Allow the Pi to reach fdbench on this laptop (direct feed, remote mode). Run once as administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\open_firewall_windows.ps1
param([int]$Port = 8791)
New-NetFirewallRule -DisplayName "fdbench $Port" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -Profile Private,Domain | Out-Null
Write-Host "Inbound TCP $Port allowed on private/domain networks. Remove with: Remove-NetFirewallRule -DisplayName 'fdbench $Port'"
