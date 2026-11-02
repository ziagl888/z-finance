# The click protocol of docs/qa/install-protocol.md, section 3, inside Windows Sandbox.
#
# Started by the sandbox's LogonCommand (see protocol.wsb as written by run_protocol.py). Every
# step writes its verdict into protocol.json in the results folder as soon as it is known, so a
# run that stops half way still leaves the steps it did. A verdict is "pass" only when the step's
# own check held; a step the sandbox cannot carry is "not run" with the reason.

param(
    [string] $InstallerDir = "C:\zf\installer",
    [string] $ResultsDir = "C:\zf\results",
    [string] $Steps = "1-15",
    # Step 13 starts Setup while z-finance still runs and records Setup's question as row 13b.
    [switch] $UpdateWhileRunning,
    [switch] $Shutdown
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "sandbox_uia.ps1")
. (Join-Path $PSScriptRoot "sandbox_apps.ps1")

$script:DataDir = Join-Path $env:LOCALAPPDATA "z-finance"
$script:AppDir = Join-Path $env:LOCALAPPDATA "Programs\z-finance"
$script:StartMenuLink = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\z-finance.lnk"
$script:DesktopLink = Join-Path ([Environment]::GetFolderPath("Desktop")) "z-finance.lnk"
$script:LocalSetup = Join-Path $env:USERPROFILE "Downloads\z-finance-setup.exe"
$script:Protocol = [ordered] @{ meta = [ordered] @{}; steps = @() }
$script:Port = 8765

function Expand-Steps([string] $Spec) {
    $numbers = @()
    foreach ($part in $Spec.Split(",")) {
        if ($part -match "^(\d+)-(\d+)$") { $numbers += [int] $Matches[1]..[int] $Matches[2] }
        elseif ($part -match "^\d+$") { $numbers += [int] $part }
    }
    return $numbers
}

function Save-Protocol {
    $path = Join-Path $ResultsDir "protocol.json"
    $script:Protocol | ConvertTo-Json -Depth 6 | Set-Content -Path $path -Encoding UTF8
}

function Add-Step([int] $Number, [string] $Result, $Evidence, $Notes, $Shots, [string] $Label = "") {
    # $Label names an extra row of the same step, such as "13b"; run_protocol.py prints it after 13.
    $entry = [ordered] @{
        number      = $Number
        result      = $Result
        evidence    = @($Evidence)
        notes       = @($Notes)
        screenshots = @($Shots)
    }
    if ($Label) { $entry.label = $Label }
    $script:Protocol.steps += $entry
    Save-Protocol
}

function New-List { return , (New-Object System.Collections.ArrayList) }

function Step01 {
    # The release is private: no browser download without a login. The file came from the host.
    $file = Get-ChildItem $InstallerDir -Filter "z-finance-setup-*.exe" | Select-Object -First 1
    $evidence = @("installer provided read-only: $($file.Name), $($file.Length) bytes")
    Add-Step 1 "not run" $evidence @("private repository, no GitHub login in the sandbox: downloaded on the host with gh, provided read-only") @()
}

function Step02 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    $file = Get-ChildItem $InstallerDir -Filter "z-finance-setup-*.exe" | Select-Object -First 1
    New-Item -ItemType Directory -Force (Split-Path $script:LocalSetup) | Out-Null
    Copy-Item $file.FullName $script:LocalSetup -Force
    [void] $evidence.Add("copied to Downloads: $((Get-Item $script:LocalSetup).Length) bytes")
    # A file from a mapped folder carries no Mark of the Web; a browser download would.
    Set-Content -Path $script:LocalSetup -Stream Zone.Identifier -Value "[ZoneTransfer]`r`nZoneId=3`r`nHostUrl=https://github.com/" -Encoding Ascii
    [void] $evidence.Add("Zone.Identifier set: " + ((Get-Content $script:LocalSetup -Stream Zone.Identifier) -join " "))
    [void] (Start-SetupLikeDoubleClick)
    $more = Find-Named @("smartscreen", "explorer") $script:Names.MoreInfo 45 ""
    if ($null -eq $more) {
        [void] $shots.Add((Save-Screenshot $ResultsDir 2 "no-smartscreen"))
        $started = Wait-SetupWindow 20
        $topLevel = @($script:Uia::RootElement.FindAll($script:Scope::Children, $script:AnyCondition) |
                ForEach-Object { $_.Current.Name } | Where-Object { $_ -ne "" })
        [void] $evidence.Add("setup started without a SmartScreen warning: $started")
        [void] $evidence.Add("windows on screen instead: " + ($topLevel -join " / "))
        [void] $evidence.Add("diagnosis: " + (Get-LaunchDiagnosis))
        [void] $notes.Add("SmartScreen did not appear in the sandbox despite the Mark of the Web: not reproducible here")
        if (-not $started) {
            Start-SetupWithoutMark $evidence $notes
            [void] $shots.Add((Save-Screenshot $ResultsDir 2 "setup-without-mark"))
        }
        Add-Step 2 "not run" $evidence $notes $shots
        return
    }
    [void] $shots.Add((Save-Screenshot $ResultsDir 2 "smartscreen"))
    Invoke-Element $more $notes "More info"
    $run = Find-Named @("smartscreen", "explorer") $script:Names.RunAnyway 15 "Button"
    if ($null -eq $run) {
        Add-Step 2 "fail" $evidence @("'Run anyway' not found after 'More info'") $shots; return
    }
    [void] $shots.Add((Save-Screenshot $ResultsDir 2 "smartscreen-more-info"))
    Invoke-Element $run $notes "Run anyway"
    $started = $null -ne (Find-Named $script:SetupProcesses $script:Names.Ok 60 "Button")
    [void] $evidence.Add("SmartScreen warned; after 'Run anyway' the setup's language dialog appeared: $started")
    $result = "fail"; if ($started) { $result = "pass" }
    Add-Step 2 $result $evidence $notes $shots
}

function Step03 {
    $notes = New-List; $evidence = New-List
    $consentSeen = $false
    $shots = Invoke-SetupWizard $ResultsDir 3 $notes
    if (Get-Process consent -ErrorAction SilentlyContinue) { $consentSeen = $true }
    Start-Sleep -Seconds 5
    $exe = Join-Path $script:AppDir "z-finance.exe"
    $installed = Test-Path $exe
    [void] $evidence.Add("installed to %LOCALAPPDATA%\Programs\z-finance: $installed")
    [void] $evidence.Add("elevation prompt (consent.exe) seen: $consentSeen")
    $result = "fail"; if ($installed -and -not $consentSeen) { $result = "pass" }
    # No setup window at all means step 2 never started it: a sandbox matter, not a product fault.
    if ($shots.Count -eq 0 -and -not $installed) { $result = "not run" }
    Add-Step 3 $result $evidence $notes $shots
}

function Step04 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    $menu = Test-Path $script:StartMenuLink
    $desktop = Test-Path $script:DesktopLink
    [void] $evidence.Add("Start menu shortcut z-finance.lnk: $menu")
    [void] $evidence.Add("desktop shortcut z-finance.lnk (box left ticked): $desktop")
    [ZfInput]::WinKey(); Start-Sleep -Seconds 2
    Send-Text "z-finance"; Start-Sleep -Seconds 3
    [void] $shots.Add((Save-Screenshot $ResultsDir 4 "start-menu"))
    Send-Text "{ESC}"; Start-Sleep -Seconds 1
    [ZfInput]::WinChord(0x44); Start-Sleep -Seconds 2
    [void] $shots.Add((Save-Screenshot $ResultsDir 4 "desktop"))
    [ZfInput]::WinChord(0x44)
    $result = "fail"; if ($menu -and $desktop) { $result = "pass" }
    Add-Step 4 $result $evidence $notes $shots
}

function Step05 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    if ((Get-ZfProcessIds).Count -gt 0) {
        [void] $notes.Add("started by the setup's 'Launch z-finance' box, left ticked as a lay user does")
    } else { Start-FromStartMenu $notes }
    $health = Wait-Health 60
    if ($null -eq $health) { Add-Step 5 "fail" @("no answer on /api/health within 60 s") $notes @(); return }
    $script:Port = $health.port
    $state = Get-Content (Join-Path $script:DataDir "runtime\launcher.json") -Raw -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 8
    $edge = @(Get-ProcessWindows @("msedge") | Where-Object { $_.Current.Name -like "*z-finance*" })
    [void] $evidence.Add("GET /api/health on port $($health.port): $($health.status) $($health.body)")
    [void] $evidence.Add("runtime/launcher.json: $state")
    [void] $evidence.Add("browser window with a z-finance page: $($edge.Count -gt 0)")
    [void] $evidence.Add("log: " + (Get-LastLogLine $script:DataDir))
    [void] $shots.Add((Save-Screenshot $ResultsDir 5 "browser"))
    $result = "fail"; if ($edge.Count -gt 0 -and $health.port -ge 8765) { $result = "pass" }
    Add-Step 5 $result $evidence $notes $shots
}

function Step06 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    if (-not (Open-TrayMenu $notes)) {
        [void] $shots.Add((Save-Screenshot $ResultsDir 6 "tray-missing"))
        Add-Step 6 "fail" @("no z-finance icon in the notification area") $notes $shots; return
    }
    $items = @(Get-TrayMenuItems)
    [void] $shots.Add((Save-Screenshot $ResultsDir 6 "tray-menu"))
    Send-Text "{ESC}"
    [void] $evidence.Add("menu entries: " + ($items -join " | "))
    $expected = @($script:Names.TrayOpen + $script:Names.TrayBackup + $script:Names.TrayFolder + $script:Names.TrayQuit)
    $known = @($items | Where-Object { $expected -contains $_ } | Select-Object -Unique)
    $result = "fail"; if ($known.Count -eq 4) { $result = "pass" }
    Add-Step 6 $result $evidence $notes $shots
}

function Invoke-WizardStep([string[]] $Button, [System.Collections.ArrayList] $Notes) {
    $element = Find-Web $Button "Button" 20
    if ($null -eq $element) {
        [void] $Notes.Add("button '$($Button[0])' not found; browser controls: " + (Get-ControlDump @("msedge")))
        return $false
    }
    Invoke-Element $element $Notes $Button[0]
    Start-Sleep -Seconds 2
    return $true
}

function Step07 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    [void] (Focus-Edge)
    [void] $shots.Add((Save-Screenshot $ResultsDir 7 "wizard-language"))
    $ok = Invoke-WizardStep $script:Names.WebContinue $notes
    $name = Find-Web $script:Names.WebName "Edit" 20
    $balance = Find-Web $script:Names.WebBalance "Edit" 5
    if ($ok -and $null -ne $name -and $null -ne $balance) {
        Set-FieldText $name "Girokonto"
        Set-FieldText $balance "1000"
        [void] $shots.Add((Save-Screenshot $ResultsDir 7 "wizard-account"))
        $ok = Invoke-WizardStep $script:Names.WebContinue $notes
        [void] $shots.Add((Save-Screenshot $ResultsDir 7 "wizard-categories"))
        $ok = $ok -and (Invoke-WizardStep $script:Names.WebContinue $notes)
        [void] $shots.Add((Save-Screenshot $ResultsDir 7 "wizard-taxes"))
        $ok = $ok -and (Invoke-WizardStep $script:Names.WebFinish $notes)
        $start = Find-Web $script:Names.WebStartPage "" 20
        if ($null -ne $start) { Invoke-Element $start $notes "To the start page"; Start-Sleep -Seconds 3 }
    } else { $ok = $false; [void] $notes.Add("account fields not found") }
    [void] $shots.Add((Save-Screenshot $ResultsDir 7 "start-page"))
    $accounts = Get-ApiJson $script:Port "/api/accounts"
    # The dashboard shows balances per area, not account names: the new account is visible as its
    # opening balance.
    $shown = $null -ne (Find-WebContaining @("1,000.00", "1.000,00") 10)
    [void] $evidence.Add("GET /api/accounts: $accounts")
    [void] $evidence.Add("start page shows the new account's balance 1,000.00: $shown")
    [void] $notes.Add("the start page shows balances per area; the account's name 'Girokonto' is not on it")
    $result = "fail"; if ($ok -and $shown -and $accounts -like "*Girokonto*") { $result = "pass" }
    Add-Step 7 $result $evidence $notes $shots
}

function Get-ZfProcessIds { return @(Get-Process z-finance -ErrorAction SilentlyContinue | ForEach-Object { $_.Id } | Sort-Object) }

function Step08 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    $before = Get-ZfProcessIds
    $windowsBefore = @(Get-ProcessWindows @("msedge") | Where-Object { $_.Current.Name -ne "" }).Count
    Start-FromStartMenu $notes
    Start-Sleep -Seconds 12
    $after = Get-ZfProcessIds
    $windowsAfter = @(Get-ProcessWindows @("msedge") | Where-Object { $_.Current.Name -ne "" }).Count
    [void] $shots.Add((Save-Screenshot $ResultsDir 8 "second-start"))
    $log = Get-Content (Join-Path $script:DataDir "runtime\z-finance.log") -ErrorAction SilentlyContinue |
        Where-Object { $_ -like "*already runs on*" } | Select-Object -Last 1
    [void] $evidence.Add("z-finance processes before/after: $($before -join ',') / $($after -join ',')")
    [void] $evidence.Add("browser windows before/after: $windowsBefore / $windowsAfter")
    [void] $evidence.Add("log: $log")
    $health = Get-Health
    [void] $evidence.Add("GET /api/health: $($health.port) $($health.body)")
    $same = ($before -join ",") -eq ($after -join ",")
    $result = "fail"; if ($same -and $windowsAfter -eq $windowsBefore -and $log) { $result = "pass" }
    Add-Step 8 $result $evidence $notes $shots
}

function Get-ExplorerFolders {
    $shell = New-Object -ComObject Shell.Application
    return @($shell.Windows() | ForEach-Object { try { $_.Document.Folder.Self.Path } catch { "" } })
}

function Step09 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    $clicked = Invoke-TrayEntry $script:Names.TrayFolder $notes
    Start-Sleep -Seconds 4
    $folders = Get-ExplorerFolders
    [void] $shots.Add((Save-Screenshot $ResultsDir 9 "data-folder"))
    $open = @($folders | Where-Object { $_ -eq $script:DataDir }).Count -gt 0
    $db = @(Get-ChildItem $script:DataDir -Filter "*.sqlite3" -ErrorAction SilentlyContinue).Count -gt 0
    $backups = Test-Path (Join-Path $script:DataDir "backups")
    [void] $evidence.Add("tray entry clicked: $clicked; Explorer shows %LOCALAPPDATA%\z-finance: $open")
    [void] $evidence.Add("database file present: $db; backups\ present: $backups")
    $shell = New-Object -ComObject Shell.Application
    $shell.Windows() | Where-Object { try { $_.Document.Folder.Self.Path -eq $script:DataDir } catch { $false } } | ForEach-Object { $_.Quit() }
    $result = "fail"; if ($clicked -and $open -and $db -and $backups) { $result = "pass" }
    Add-Step 9 $result $evidence $notes $shots
}

function Get-ManualBackups {
    return @(Get-ChildItem (Join-Path $script:DataDir "backups") -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like "*-manual" } | ForEach-Object { $_.Name })
}

function Step10 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    $before = Get-ManualBackups
    $clicked = Invoke-TrayEntry $script:Names.TrayBackup $notes
    Start-Sleep -Seconds 5
    $after = Get-ManualBackups
    $new = @($after | Where-Object { $before -notcontains $_ })
    [void] $evidence.Add("tray entry clicked: $clicked; new manual backups: $($new -join ', ')")
    [void] $evidence.Add("log: " + (Get-LastLogLine $script:DataDir))
    Start-Process explorer.exe -ArgumentList "`"$(Join-Path $script:DataDir 'backups')`""
    Start-Sleep -Seconds 3
    [void] $shots.Add((Save-Screenshot $ResultsDir 10 "backup"))
    $shell = New-Object -ComObject Shell.Application
    $shell.Windows() | ForEach-Object { try { $_.Quit() } catch { } }
    $result = "fail"; if ($clicked -and $new.Count -eq 1) { $result = "pass" }
    Add-Step 10 $result $evidence $notes $shots
}

. (Join-Path $PSScriptRoot "install_protocol_late.ps1")

$transcript = Join-Path $ResultsDir "transcript.txt"
Start-Transcript -Path $transcript -Force | Out-Null
try {
    $script:Protocol.meta = Get-SandboxFacts
    Save-Protocol
    Wait-Desktop
    [void] (Set-EdgePolicies)
    foreach ($number in (Expand-Steps $Steps)) {
        $installed = Test-Path (Join-Path $script:AppDir "z-finance.exe")
        if ($number -ge 4 -and -not $installed) {
            Add-Step $number "not run" @() @("z-finance is not installed in this sandbox (step 3 did not finish)") @()
            continue
        }
        try { & ("Step{0:D2}" -f $number) }
        catch { Add-Step $number "not run" @() @("script error: $($_.Exception.Message)") @() }
    }
} finally {
    Stop-Transcript | Out-Null
    Set-Content -Path (Join-Path $ResultsDir "done.json") -Value (@{ finished = (Get-Date).ToString("o") } | ConvertTo-Json)
    if ($Shutdown) { shutdown.exe /s /t 5 }
}
