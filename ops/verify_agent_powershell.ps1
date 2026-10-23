# Credentials are supplied through stdin, not command arguments, URLs or logs.
$ErrorActionPreference = 'Stop'
$agentProbe = [Console]::In.ReadToEnd() | ConvertFrom-Json
$agentReply = Invoke-RestMethod -Method Get -Uri ($agentProbe.endpoint + '/context') -Headers @{ Authorization = ('Bearer ' + $agentProbe.key) } -TimeoutSec 30
if ($agentReply.server_id -ne $agentProbe.vm_id) { throw 'Unexpected VM scope' }
$agentProbe.key = ''
[Console]::Out.WriteLine('Built-in PowerShell HTTPS API connection verified')
