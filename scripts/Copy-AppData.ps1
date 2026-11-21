<#
.SYNOPSIS
Copies the data of one TurkBizTel stack into another, once.

.DESCRIPTION
The single sign-on stack keeps its own database, so it starts without the enterprises, partners and orders that the
access-token stack has gathered. This seeds it from that stack and leaves the two apart: it is a one-time, one-way
snapshot, nothing is synchronised afterwards, and the source stack is only read.

Copied: enterprise accounts and partner contacts (with their password hash, so a password keeps working), partner
organisations, orders with their messages, partner concerns, idempotency keys, the event history, the audit trail (added
to the end of the target's own chain and signed again; each entry says which entry it was) and the capacity units that
the copied orders hold.

Left behind: sessions; one-time e-mail codes; invitation, approval and tracking links (each is signed with the secret of
the stack that issued it); the benchmark simulation; and orders that are still in progress (the agents are working on
them, they wait for an enterprise's approval link, or the source is still sending the customer reminders), which a later
run copies once they have settled. A message that the source has queued but not yet sent is copied as "copied-not-sent":
the source sends it, and the target must not send it a second time.

A record the target already holds is never changed, so a second run copies only what is new. An e-mail address or name
that a different record holds in the target is reported as a conflict and left alone.

Both databases are private, so each step runs inside the container of the stack it touches (az containerapp exec). The
data passes through this computer's memory only: it is never printed or written to a file here, and the output shows
counts, never names or e-mail addresses. It is staged for a few minutes in a private folder of the target's container
and removed at the end.

The Azure CLI account must be allowed to run commands in both container apps (Contributor on both resource groups). A
run takes a while: about fifty exec calls, and Azure limits how often a session may be opened.

.PARAMETER DryRun
Reads and exports everything and checks it against the target, but writes nothing.

.EXAMPLE
.\scripts\Copy-AppData.ps1 -DryRun

.EXAMPLE
.\scripts\Copy-AppData.ps1
#>
[CmdletBinding()]
param(
    [string]$SourceReceipt = 'deployment.local.json',
    [string]$TargetReceipt = 'deployment.sso.local.json',
    [switch]$DryRun,
    [ValidateRange(30, 900)][int]$TimeoutSeconds = 240
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Read-Stack([string]$Path) {
    $full = if ([IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $root $Path }
    if (-not (Test-Path $full)) { throw "Deployment receipt not found: $Path" }
    $receipt = Get-Content -Raw $full | ConvertFrom-Json
    foreach ($key in 'appName', 'resourceGroup', 'subscriptionId') {
        if (-not $receipt.$key) { throw "$Path has no $key." }
    }
    [pscustomobject]@{ App = $receipt.appName; Group = $receipt.resourceGroup; Subscription = $receipt.subscriptionId }
}

# az.cmd cannot pass the quotes of a command through, so run the Azure CLI with the Python it ships with.
$azure = Get-Command az.cmd -ErrorAction SilentlyContinue
if (-not $azure) { $azure = Get-Command az -ErrorAction Stop }
$cliPython = Join-Path (Split-Path -Parent (Split-Path -Parent $azure.Source)) 'python.exe'
if (-not (Test-Path $cliPython)) { throw "The Azure CLI's Python was not found at $cliPython." }

# Neither image is rebuilt. exec carries its command in a URL, which the platform refuses beyond about 1,800 characters
# (1,839 were accepted, 2,039 were not), and splits it on whitespace (hence the expressions below have none). So the
# helper, compressed, and the batch are first written in pieces into a private folder of the container, then run from
# there. A piece of 1,500 characters makes a command of about 1,650 once encoded for the URL.
$work = '/tmp/ama-copy'
$pieceSize = 1500
$commandLimit = 1780
$staged = @{}
$touched = @{}
$bytes = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'Copy_Data.py'))
$buffer = New-Object IO.MemoryStream
$deflate = New-Object IO.Compression.DeflateStream($buffer, [IO.Compression.CompressionMode]::Compress)
$deflate.Write($bytes, 0, $bytes.Length)
$deflate.Close()
$packed = [Convert]::ToBase64String($buffer.ToArray()).Replace('+', '-').Replace('/', '_')
$program = "python -c exec(__import__('zlib').decompress(__import__('base64').urlsafe_b64decode(open('$work/h').read()),-15))"

# One command in one container. Calls to the same app must not overlap (the platform refuses to issue several exec
# tokens at once), so they run one after the other; a refusal or a dropped connection is tried again. The platform also
# limits how often exec sessions may be opened (429 Too Many Requests); that clears after a few minutes, so it is waited
# out with growing pauses instead of being given up on after seconds.
function Invoke-Exec($Stack, [string]$Command, [string]$Success) {
    $encoded = [Uri]::EscapeDataString($Command).Length
    if ($encoded -gt $commandLimit) { throw "Internal error: a command of $encoded characters is too long for az containerapp exec." }
    $lines = @()
    $timedOut = $false
    $attempt = 0
    while ($true) {
        $attempt++
        $job = Start-Job -ScriptBlock {
            param($Python, $App, $Group, $Subscription, $Command)
            $ErrorActionPreference = 'Continue'
            & $Python -IBm azure.cli containerapp exec -n $App -g $Group --subscription $Subscription --command $Command 2>&1 |
                ForEach-Object { "$_" }
        } -ArgumentList $cliPython, $Stack.App, $Stack.Group, $Stack.Subscription, $Command
        try {
            $timedOut = -not (Wait-Job $job -Timeout $TimeoutSeconds)
            $lines = if ($timedOut) { @() } else {
                @(Receive-Job $job | ForEach-Object { ($_ -split "`r?`n") } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
            }
        }
        finally { Remove-Job $job -Force }
        $failure = $lines | Where-Object { $_ -match '^ERROR \w+:' } | Select-Object -First 1
        if (-not $failure -and ($lines | Where-Object { $_ -match $Success })) { return $lines }
        if ($failure -or @($lines | Where-Object { $_ -match 'FileNotFoundError' }).Count) { break }
        $limited = @($lines | Where-Object { $_ -match 'Too Many Requests' }).Count -gt 0
        if ($attempt -ge $(if ($limited) { 5 } else { 3 })) { break }
        $pause = if ($limited) { 60 * $attempt } else { 5 * $attempt }
        if ($limited) { Write-Host "  Azure is limiting exec sessions on $($Stack.App); trying again in $pause seconds ..." }
        Start-Sleep -Seconds $pause
    }
    # Never echo data: only short sentences, not the batch lines or markers, may appear in a failure.
    $notes = @($lines | Where-Object { $_ -match '\s' -and $_ -notmatch '^(RESULT|BEGIN|END)[ -]' } | Select-Object -Last 4 |
        ForEach-Object { $_.Substring(0, [Math]::Min($_.Length, 200)) })
    if ($timedOut) { $notes = @("No answer within $TimeoutSeconds seconds.") }
    throw "$($Stack.App) did not return a result. $($notes -join ' | ')"
}

# Writes text into the container, piece by piece; each piece goes to its own offset, so repeating one is harmless.
function Send-File($Stack, [string]$Name, [string]$Text) {
    $touched[$Stack.App] = $Stack
    if (-not $staged["$($Stack.App)/dir"]) {
        $null = Invoke-Exec $Stack "python -c print('RESULT-'+str(__import__('os').makedirs('$work',448,True)))" '^RESULT-'
        $staged["$($Stack.App)/dir"] = $true
    }
    $pieces = [Math]::Ceiling($Text.Length / $pieceSize)
    for ($index = 0; $index -lt $pieces; $index++) {
        $at = $index * $pieceSize
        $piece = $Text.Substring($at, [Math]::Min($pieceSize, $Text.Length - $at))
        $flags = if ($at -eq 0) { 578 } else { 66 }   # create; the first piece also empties any leftover file
        $command = "python -c print(__import__('os').pwrite(__import__('os').open('$work/$Name',$flags,384),b'$piece',$at))"
        $null = Invoke-Exec $Stack $command ('^{0}$' -f $piece.Length)
        if ($pieces -gt 1) { Write-Host ("  {0} on {1}: piece {2}/{3}" -f $(if ($Name -eq 'h') { 'program' } else { 'batch' }), $Stack.App, ($index + 1), $pieces) }
    }
}

# Runs the helper in a stack's container (staging it first), optionally on a batch staged as the file b.
function Invoke-Helper($Stack, [string[]]$Arguments, [string]$Batch) {
    for ($try = 1; $try -le 2; $try++) {
        if (-not $staged["$($Stack.App)/h"]) { Send-File $Stack 'h' $packed; $staged["$($Stack.App)/h"] = $true }
        if ($Batch -and -not $staged["$($Stack.App)/b"]) { Send-File $Stack 'b' $Batch; $staged["$($Stack.App)/b"] = $true }
        try {
            $lines = Invoke-Exec $Stack "$program $($Arguments -join ' ')" '^RESULT \{'
            $line = $lines | Where-Object { $_ -match '^RESULT \{' } | Select-Object -First 1
            return [pscustomobject]@{ Result = ($line.Substring(7) | ConvertFrom-Json); Lines = $lines }
        }
        catch {
            # A restarted container has lost the staged files, and a damaged batch is simply sent again.
            if ($try -eq 2 -or $_.Exception.Message -notmatch 'FileNotFoundError|batch arrived damaged') { throw }
            foreach ($key in @($staged.Keys | Where-Object { $_ -like "$($Stack.App)/*" })) { $staged.Remove($key) }
        }
    }
}

function Remove-Staged($Stack) {
    try { $null = Invoke-Exec $Stack "python -c print('RESULT-'+str(__import__('shutil').rmtree('$work',True)))" '^RESULT-' }
    catch { Write-Warning "Could not remove $work from $($Stack.App): $($_.Exception.Message)" }
}

# The batch the export printed between its markers, and whether it arrived whole: its length and the checksum that its
# own first characters carry.
function Get-Batch([string[]]$Lines) {
    $text = New-Object Text.StringBuilder
    $open = $false
    foreach ($line in $Lines) {
        if ($line -match '^BEGIN \d+$') { $open = $true }
        elseif ($line -match '^END \d+$') { $open = $false }
        elseif ($open -and $line -match '^[A-Za-z0-9_=.-]+$') { [void]$text.Append($line) }
    }
    $text.ToString()
}

function Test-Batch([string]$Batch, [int]$Size) {
    $checksum, $body = $Batch -split '\.', 2
    if (-not $body -or $Batch.Length -ne $Size) { return $false }
    $hash = [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::ASCII.GetBytes($body))
    $checksum -eq (($hash | ForEach-Object { $_.ToString('x2') }) -join '').Substring(0, 16)
}

function Show-Summary([string]$Label, $Stack, $Summary) {
    $states = ($Summary.states.PSObject.Properties | Sort-Object Name | ForEach-Object { "$($_.Value) $($_.Name)" }) -join ', '
    Write-Host ("{0,-7}{1}: {2} orders{3}; {4} enterprise accounts ({5} verified); {6} partners, {7} partner contacts, {8} concerns; {9} events; {10} audit entries ({11}); {12} capacity units free" -f
        $Label, $Stack.App, $Summary.orders, $(if ($states) { " ($states)" } else { '' }), $Summary.customers,
        $Summary.verified, $Summary.partners, $Summary.partnerUsers, $Summary.concerns, $Summary.events, $Summary.audit,
        $(if ($Summary.auditIntact) { 'chain intact' } else { 'CHAIN BROKEN' }), $Summary.units)
}

$tables = [ordered]@{
    customers = 'Enterprise accounts'; partners = 'Partners'; partnerUsers = 'Partner contacts'; orders = 'Orders'
    concerns = 'Concerns'; idempotency = 'Idempotency keys'; events = 'Events'; audit = 'Audit entries'
}

# What an import did, or would do, per kind of record; returns how many records were left out for a reason that needs a look.
function Show-Report($Report, [bool]$Dry) {
    Write-Host ("  {0,-20}{1,9}{2,15}{3,10}{4,9}{5,10}{6,6}" -f 'Records', $(if ($Dry) { 'to copy' } else { 'copied' }), 'already there', 'conflict', 'invalid', 'unlinked', 'full')
    $attention = 0
    foreach ($key in $tables.Keys) {
        $row = $Report.$key
        Write-Host ("  {0,-20}{1,9}{2,15}{3,10}{4,9}{5,10}{6,6}" -f $tables[$key], $row.copied, $row.existing, $row.conflict, $row.invalid, $row.orphan, $row.full)
        # An audit entry about a record the target does not hold (a benchmark run, say) is expected to stay behind.
        $attention += [int]$row.conflict + [int]$row.invalid + [int]$row.full + $(if ($key -eq 'audit') { 0 } else { [int]$row.orphan })
    }
    Write-Host ("  Capacity units taken by the copied orders: {0}. Messages marked copied-not-sent: {1}." -f $Report.capacity, $Report.unsent)
    Write-Host ("  Orders still in progress, left for a later run: {0}. Source audit chain intact: {1}." -f $Report.heldBack, $Report.auditIntact)
    Write-Host ("  Partner contacts without a password in the target (invite them again from the partner page): {0}." -f $Report.needInvite)
    $attention
}

# Lets a throwaway script borrow the functions above (. .\scripts\Copy-AppData.ps1) without starting a copy.
if ($MyInvocation.InvocationName -eq '.') { return }

$source = Read-Stack $SourceReceipt
$target = Read-Stack $TargetReceipt
if ($source.App -eq $target.App -and $source.Group -eq $target.Group) { throw 'The source and the target are the same app.' }

try {
    Write-Host "Reading $($source.App) and $($target.App) ..."
    $sourceBefore = (Invoke-Helper $source @('summary')).Result
    $targetBefore = (Invoke-Helper $target @('summary')).Result
    Show-Summary 'Source ' $source $sourceBefore
    Show-Summary 'Target ' $target $targetBefore

    Write-Host 'Exporting from the source ...'
    $export = Invoke-Helper $source @('export')
    $batch = Get-Batch $export.Lines
    if (-not (Test-Batch $batch ([int]$export.Result.size))) { throw 'The export arrived incomplete or damaged. Nothing was changed.' }
    Write-Host ("Exported {0} orders, {1} enterprise accounts, {2} partners, {3} partner contacts, {4} concerns, {5} events and {6} audit entries ({7} characters); {8} orders still in progress stay behind." -f
        $export.Result.orders, $export.Result.customers, $export.Result.partners, $export.Result.partnerUsers,
        $export.Result.concerns, $export.Result.events, $export.Result.audit, $export.Result.size, $export.Result.heldBack)

    $file = "@$work/b"
    Write-Host 'Checking the batch against the target ...'
    $check = (Invoke-Helper $target @('import', $file, '--dry-run') $batch).Result
    $left = Show-Report $check $true
    if ($DryRun) {
        if ($left) { Write-Warning "$left records would not be copied; see the table above." }
        Write-Host 'Dry run: nothing was written. Run it again without -DryRun to copy.'
        return
    }

    Write-Host 'Copying ...'
    $done = (Invoke-Helper $target @('import', $file) $batch).Result
    $left = Show-Report $done $false
    $targetAfter = (Invoke-Helper $target @('summary')).Result
    $sourceAfter = (Invoke-Helper $source @('summary')).Result
    Show-Summary 'Source ' $source $sourceAfter
    Show-Summary 'Target ' $target $targetAfter
    Write-Host ("Target audit trail intact: {0}. Source orders before and after: {1} and {2}." -f $targetAfter.auditIntact,
        $sourceBefore.orders, $sourceAfter.orders)
    if ($left) { Write-Warning "$left records were not copied; see the table above." }
}
finally {
    foreach ($stack in @($touched.Values)) { Remove-Staged $stack }
}
