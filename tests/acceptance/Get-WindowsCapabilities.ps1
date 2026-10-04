param([string]$ServiceName='PassiveListener',[string]$TaskName='PassiveListenerArchive')
$ErrorActionPreference='Stop'
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor
$service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
[ordered]@{
    OS=$os.Caption; OSVersion=$os.Version; MemoryKiB=$os.TotalVisibleMemorySize
    CPU=@($cpu | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors)
    PowerShell=$PSVersionTable.PSVersion.ToString()
    ServicePresent=($null -ne $service); ScheduledTaskPresent=($null -ne $task)
    Microphone='not opened; explicit smoke only'
} | ConvertTo-Json -Depth 5
