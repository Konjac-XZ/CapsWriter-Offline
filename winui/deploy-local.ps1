[CmdletBinding()]
param(
    [switch]$Launch
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$winuiRoot = $PSScriptRoot
$repositoryRoot = Split-Path -Parent $winuiRoot
$projectDirectory = Join-Path $winuiRoot 'CapsWriter.WinUI'
$projectPath = Join-Path $projectDirectory 'CapsWriter.WinUI.csproj'
$nugetConfig = Join-Path $winuiRoot 'NuGet.Config'
$packageName = '9DBD0288-0A22-44C1-BCC4-77F08036742D'
$expectedProcessRoot = [IO.Path]::GetFullPath($projectDirectory).TrimEnd('\') + '\'

Write-Host 'Stopping deployed CapsWriter Native instances from this checkout...'
$ownedProcesses = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq 'CapsWriter.WinUI.exe' -and
        -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
        [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
            $expectedProcessRoot,
            [StringComparison]::OrdinalIgnoreCase
        )
    }

foreach ($process in $ownedProcesses) {
    & taskkill.exe /PID $process.ProcessId /T /F | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stop CapsWriter Native process $($process.ProcessId)."
    }
}

Write-Host 'Restoring and building the Release package layout...'
& dotnet restore $projectPath --configfile $nugetConfig
if ($LASTEXITCODE -ne 0) {
    throw 'dotnet restore failed.'
}
& dotnet build $projectPath -c Release --no-restore
if ($LASTEXITCODE -ne 0) {
    throw 'dotnet build failed.'
}

Write-Host 'Registering CapsWriter Native for the current user...'
& dotnet run --project $projectPath -c Release --no-build -- --no-launch
if ($LASTEXITCODE -ne 0) {
    throw 'WinApp registration failed.'
}

$package = Get-AppxPackage -Name $packageName |
    Sort-Object InstallLocation -Descending |
    Select-Object -First 1
if ($null -eq $package -or $package.Status -ne 'Ok') {
    throw 'The CapsWriter Native package is not registered with status Ok.'
}

$registeredLocation = [IO.Path]::GetFullPath($package.InstallLocation)
$expectedReleaseRoot = [IO.Path]::GetFullPath(
    (Join-Path $projectDirectory 'bin\Release')
).TrimEnd('\') + '\'
if (-not $registeredLocation.StartsWith(
    $expectedReleaseRoot,
    [StringComparison]::OrdinalIgnoreCase
)) {
    throw "Package points to an unexpected location: $registeredLocation"
}

$aumid = "$($package.PackageFamilyName)!App"
$startEntry = $null
for ($attempt = 0; $attempt -lt 10 -and $null -eq $startEntry; $attempt++) {
    $startEntry = Get-StartApps |
        Where-Object { $_.AppID -eq $aumid } |
        Select-Object -First 1
    if ($null -eq $startEntry) {
        Start-Sleep -Milliseconds 500
    }
}
if ($null -eq $startEntry) {
    throw "Start menu entry was not found for $aumid."
}
if ($startEntry.Name -ne 'CapsWriter Native') {
    throw "Unexpected Start menu name: $($startEntry.Name)"
}

Write-Host ''
Write-Host 'CapsWriter Native deployed successfully.' -ForegroundColor Green
Write-Host "Start menu: $($startEntry.Name)"
Write-Host "AUMID:      $aumid"
Write-Host "Location:   $registeredLocation"

if ($Launch) {
    Write-Host 'Launching the deployed Start menu application...'
    $launchStartedAt = Get-Date
    Start-Process explorer.exe "shell:AppsFolder\$aumid"
    $launchedProcess = $null
    for ($attempt = 0; $attempt -lt 20 -and $null -eq $launchedProcess; $attempt++) {
        Start-Sleep -Milliseconds 500
        $launchedProcess = Get-CimInstance Win32_Process |
            Where-Object {
                $_.Name -eq 'CapsWriter.WinUI.exe' -and
                -not [string]::IsNullOrWhiteSpace($_.ExecutablePath) -and
                [IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
                    $registeredLocation.TrimEnd('\') + '\',
                    [StringComparison]::OrdinalIgnoreCase
                )
            } |
            Select-Object -First 1
    }
    if ($null -eq $launchedProcess) {
        throw 'CapsWriter Native did not remain running after Start menu activation.'
    }
    Start-Sleep -Seconds 2
    $survivingProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($launchedProcess.ProcessId)"
    if ($null -eq $survivingProcess) {
        throw 'CapsWriter Native exited during post-launch verification.'
    }
    $launchErrors = @(
        Get-WinEvent -FilterHashtable @{
            LogName = 'Application'
            StartTime = $launchStartedAt
        } -ErrorAction SilentlyContinue |
            Where-Object {
                $_.LevelDisplayName -eq 'Error' -and
                $_.Message -like '*CapsWriter.WinUI*'
            }
    )
    if ($launchErrors.Count -ne 0) {
        throw 'Windows logged an application error after launching CapsWriter Native.'
    }
    Write-Host "Process:    $($survivingProcess.ProcessId) (launch verified)"
}
