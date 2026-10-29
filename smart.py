"""SMART reader: health, temperature and wear for each physical disk, read through the Windows storage cmdlets.

Counters such as temperature need an elevated process. Missing values come back as None, never as a guess.
"""

import json
import subprocess

_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ErrorActionPreference = 'Stop'
$parts = Get-Partition | Where-Object DriveLetter
$disks = @(Get-Disk)
$legacyLoaded = $false
$legacy = @()
$winDisks = @()
$out = foreach ($d in Get-PhysicalDisk) {
    $c = $null
    try { $c = $d | Get-StorageReliabilityCounter -ErrorAction Stop } catch {}
    # Match stable device identity, not PhysicalDisk.DeviceId to Disk.Number.
    $matches = @($disks | Where-Object {
        ($d.UniqueId -and $_.UniqueId -eq $d.UniqueId) -or
        ($d.SerialNumber -and $_.SerialNumber -and $_.SerialNumber.Trim() -eq $d.SerialNumber.Trim())
    })
    $numbers = @($matches | ForEach-Object { $_.Number })
    $rawSmart = $null
    if (($null -eq $c.Temperature -or $c.Temperature -le 0) -and $d.BusType -in @('SATA','ATA','RAID','SAS')) {
        if (-not $legacyLoaded) {
            $legacyLoaded = $true
            try { $legacy = @(Get-CimInstance -Namespace root/wmi -ClassName MSStorageDriver_FailurePredictData -ErrorAction Stop) } catch {}
            try { $winDisks = @(Get-CimInstance -ClassName Win32_DiskDrive -ErrorAction Stop) } catch {}
        }
        $devices = @($winDisks | Where-Object { $numbers -contains $_.Index })
        $sensorMatches = @($legacy | Where-Object {
            $instance = $_.InstanceName.ToUpperInvariant()
            @($devices | Where-Object {
                $pnp = $_.PNPDeviceID.ToUpperInvariant()
                $instance -eq $pnp -or $instance.StartsWith($pnp + '_')
            }).Count -gt 0
        })
        if ($sensorMatches.Count -eq 1) { $rawSmart = @($sensorMatches[0].VendorSpecific) }
    }
    [pscustomobject]@{
        Number      = $d.DeviceId
        Name        = $d.FriendlyName
        Bus         = [string]$d.BusType
        Media       = [string]$d.MediaType
        Health      = [string]$d.HealthStatus
        Letters     = @($parts | Where-Object { $numbers -contains $_.DiskNumber } | ForEach-Object { [string]$_.DriveLetter })
        Temperature = $c.Temperature
        AtaSmart    = $rawSmart
        TempMax     = $c.TemperatureMax
        Wear        = $c.Wear
        PowerOnHours = $c.PowerOnHours
    }
}
$out | ConvertTo-Json -Depth 3 -Compress
"""


def valid_temperature(value):
    # Windows uses zero for unsupported counters on some SATA SSDs.
    if type(value) in (int, float) and 0 < value <= 125:
        return value
    return None


def ata_temperature(data):
    """Read raw ATA Celsius attributes; never use their normalized health score."""
    if not isinstance(data, (bytes, bytearray, list)) or len(data) < 362:
        return None
    attributes = {}
    for offset in range(2, 362, 12):
        ident = data[offset]
        raw = data[offset + 5]
        if ident in (194, 190):
            attributes[ident] = valid_temperature(raw)
    return attributes.get(194) or attributes.get(190)


def read_disks() -> list:
    """Return one dict per physical disk. An empty list means the query failed."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=0x08000040)  # CREATE_NO_WINDOW
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    records = data if isinstance(data, list) else [data]
    disks = [disk for disk in records if isinstance(disk, dict)]
    for disk in disks:
        temp = valid_temperature(disk.get("Temperature"))
        source = "Windows storage counter" if temp is not None else None
        if temp is None:
            temp = ata_temperature(disk.get("AtaSmart"))
            if temp is not None:
                source = "ATA SMART attribute"
        disk.pop("AtaSmart", None)
        disk["Temperature"] = temp
        disk["TemperatureSource"] = source
    return disks


def letters_map() -> dict:
    """Map a drive letter like 'C:' to its disk record."""
    mapping = {}
    for disk in read_disks():
        for letter in disk.get("Letters") or []:
            mapping[f"{letter}:"] = disk
    return mapping
