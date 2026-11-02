# Steps 11-15 of the click protocol and the run's set-up, dot-sourced by install_protocol.ps1.
#
# Runs inside Windows Sandbox only. Kept apart from the first ten steps so neither file grows past
# a size one can read in one sitting.

$script:BookingText = "Sandbox protocol booking"

function Get-SandboxFacts {
    $version = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    return [ordered] @{
        started        = (Get-Date).ToString("o")
        os_version     = [Environment]::OSVersion.VersionString
        display        = "$($version.ProductName) $($version.DisplayVersion) (build $($version.CurrentBuild).$($version.UBR))"
        ui_culture     = (Get-UICulture).Name
        user           = $env:USERNAME
        python_on_path = $null -ne (Get-Command python.exe -ErrorAction SilentlyContinue | Where-Object { $_.Source -notlike "*WindowsApps*" })
        node_on_path   = $null -ne (Get-Command node.exe -ErrorAction SilentlyContinue)
        zfinance_before = Test-Path (Join-Path $env:LOCALAPPDATA "Programs\z-finance")
    }
}

function Wait-Desktop {
    # The LogonCommand starts before the taskbar is ready; clicks before that land nowhere.
    $deadline = (Get-Date).AddSeconds(120)
    do {
        $tray = $script:Uia::RootElement.FindAll($script:Scope::Children, $script:AnyCondition) |
            Where-Object { $_.Current.ClassName -eq "Shell_TrayWnd" }
        if ($tray) { break }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    Start-Sleep -Seconds 15
}

function Test-Booking {
    return (Get-ApiJson $script:Port "/api/transactions") -like "*$script:BookingText*"
}

function Add-Booking([System.Collections.ArrayList] $Notes) {
    if (-not (Open-InEdge "127.0.0.1:$script:Port/entry")) { [void] $Notes.Add("no browser window"); return $false }
    $amount = Find-Web $script:Names.WebAmount "Edit" 20
    # The text field has suggestions (a datalist), so the browser reports it as a combo box.
    $text = Find-Web $script:Names.WebText "ComboBox" 5
    $category = Find-Web $script:Names.WebCategory "ComboBox" 5
    if ($null -eq $amount -or $null -eq $text -or $null -eq $category) {
        [void] $Notes.Add("quick entry fields not found (amount/text/category: $($null -ne $amount)/$($null -ne $text)/$($null -ne $category)); browser controls: " + (Get-ControlDump @("msedge")))
        return $false
    }
    Set-FieldText $amount "12,34"
    Set-FieldText $text $script:BookingText
    Click-Element $category $false; Start-Sleep -Milliseconds 500
    Send-Text "{DOWN}{ENTER}"; Start-Sleep -Milliseconds 300
    $save = Find-Web $script:Names.WebSave "Button" 5
    if ($null -eq $save) { [void] $Notes.Add("'Save' not found"); return $false }
    Invoke-Element $save $Notes "Save"
    Start-Sleep -Seconds 3
    return $true
}

function Stop-ByTray([System.Collections.ArrayList] $Notes) {
    $clicked = Invoke-TrayEntry $script:Names.TrayQuit $Notes
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-ZfProcessIds).Count -gt 0 -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 1 }
    return $clicked -and (Get-ZfProcessIds).Count -eq 0
}

function Get-LogTail([int] $Lines = 3) {
    $log = Join-Path $script:DataDir "runtime\z-finance.log"
    return ((Get-Content $log -Tail $Lines -ErrorAction SilentlyContinue) -join " | ")
}

function Stop-HungZfinance($Evidence, [System.Collections.ArrayList] $Notes) {
    # Workaround for a z-finance.exe that outlives "Quit" (T-2026-HAR-9130-022): end it the way a
    # user would in Task Manager, so the later steps test their own subject. Sandbox only.
    $left = @(Get-ZfProcessIds)
    if ($left.Count -eq 0) { return }
    [void] $Evidence.Add("still running after 'Quit': z-finance.exe $($left -join ','); log: " + (Get-LogTail))
    $left | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
    [void] $Notes.Add("workaround: the z-finance.exe left after 'Quit' was ended like in Task Manager (T-2026-HAR-9130-022)")
    Start-Sleep -Seconds 2
}

function Step11 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    $entered = Add-Booking $notes
    [void] $shots.Add((Save-Screenshot $ResultsDir 11 "booking"))
    $stored = Test-Booking
    $stopped = Stop-ByTray $notes
    Start-Sleep -Seconds 2
    $icon = Find-NamedPrefix @("explorer") "z-finance" 3
    Send-Text "{ESC}"
    [void] (Focus-Edge); Send-Text "{F5}"; Start-Sleep -Seconds 4
    [void] $shots.Add((Save-Screenshot $ResultsDir 11 "after-quit"))
    $health = Get-Health
    [void] $evidence.Add("booking entered through the form: $entered; GET /api/transactions holds it: $stored")
    [void] $evidence.Add("after 'Quit': no z-finance process: $stopped; tray icon still found: $($null -ne $icon)")
    [void] $evidence.Add("GET /api/health after quit answers: $($null -ne $health)")
    [void] $evidence.Add("z-finance.exe after 20 s: $((Get-ZfProcessIds) -join ','); log: " + (Get-LogTail))
    $result = "fail"; if ($entered -and $stored -and $stopped -and $null -eq $icon -and $null -eq $health) { $result = "pass" }
    Add-Step 11 $result $evidence $notes $shots
}

function Step12 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    Start-FromStartMenu $notes
    $health = Wait-Health 60
    if ($null -ne $health) { $script:Port = $health.port }
    [void] (Open-InEdge "127.0.0.1:$script:Port/entry")
    $shown = $null -ne (Find-Web @($script:BookingText) "" 15)
    [void] $shots.Add((Save-Screenshot $ResultsDir 12 "booking-kept"))
    $stored = Test-Booking
    [void] $evidence.Add("GET /api/health: $($health.port) $($health.body)")
    [void] $evidence.Add("booking listed on the entry page: $shown; in GET /api/transactions: $stored")
    $result = "fail"; if ($null -ne $health -and $shown -and $stored) { $result = "pass" }
    if ($result -eq "fail") { Test-RestartAfterWorkaround $evidence $notes $shots }
    Add-Step 12 $result $evidence $notes $shots
}

function Test-RestartAfterWorkaround($Evidence, [System.Collections.ArrayList] $Notes, $Shots) {
    # The start failed. Is the booking still there once the left-over process is gone? This does
    # not turn the step into a pass; it tells whether the data survived.
    [void] $Evidence.Add("log after the failed start: " + (Get-LogTail 4))
    Stop-HungZfinance $Evidence $Notes
    Start-FromStartMenu $Notes
    $health = Wait-Health 60
    if ($null -ne $health) { $script:Port = $health.port }
    [void] (Open-InEdge "127.0.0.1:$script:Port/entry")
    $shown = $null -ne (Find-Web @($script:BookingText) "" 15)
    [void] $Shots.Add((Save-Screenshot $ResultsDir 12 "after-workaround"))
    [void] $Evidence.Add("after the workaround: server answers: $($null -ne $health); booking listed: $shown; in the API: $(Test-Booking)")
}

$script:AppRunning = [ordered] @{ asked = 0; text = ""; quit = $false; left = "" }
$script:CloseFailedSeen = $false

function Resolve-AppRunningQuestion($Window, [string] $ResultsDir, [int] $Step, [System.Collections.ArrayList] $Notes, $Shots) {
    # Setup found z-finance's mutex and asks to close it. Answer as the manual says: "Quit" in the
    # tray, then OK. Called by Invoke-SetupWizard; the outcome is read by Step13WhileRunning.
    $script:AppRunning.asked++
    $script:AppRunning.text = Get-WindowTexts $Window
    [void] $Shots.Add((Save-Screenshot $ResultsDir $Step "setup-asks-to-close"))
    $script:AppRunning.quit = Stop-ByTray $Notes
    $script:AppRunning.left = (Get-ZfProcessIds) -join ","
    $ok = Find-NamedBelow $Window $script:Names.Ok ""
    if ($null -eq $ok) { [void] $Notes.Add("'OK' not found in Setup's question"); return }
    Invoke-Element $ok $Notes "OK (after Quit)"
    Start-Sleep -Seconds 2
}

function Start-UpdateSetup([System.Collections.ArrayList] $Notes, [System.Collections.ArrayList] $Shots) {
    [void] (Start-SetupLikeDoubleClick)
    $more = Find-Named @("smartscreen", "explorer") $script:Names.MoreInfo 20 ""
    if ($null -ne $more) {
        Invoke-Element $more $Notes "More info"
        $run = Find-Named @("smartscreen", "explorer") $script:Names.RunAnyway 15 "Button"
        if ($null -ne $run) { Invoke-Element $run $Notes "Run anyway" }
    }
    $pages = Invoke-SetupWizard $ResultsDir 13 $Notes
    foreach ($page in $pages) { [void] $Shots.Add($page) }
    return @($pages | Where-Object { $_ -like "*setup-finish*" }).Count -gt 0
}

function Step13 {
    if ($UpdateWhileRunning) { Step13WhileRunning; return }
    $notes = New-List; $shots = New-List; $evidence = New-List
    $stopped = Stop-ByTray $notes
    [void] $evidence.Add("program quit through the tray before the update: $stopped")
    Stop-HungZfinance $evidence $notes
    $exeBefore = (Get-Item (Join-Path $script:AppDir "z-finance.exe")).LastWriteTime
    [void] (Start-UpdateSetup $notes $shots)
    Complete-Step13 $exeBefore $evidence $notes $shots
}

function Step13WhileRunning {
    # Row 13b: the update starts while z-finance runs; Setup must ask in plain words, and go on
    # after a tray "Quit" and OK. No "Ignore the error", no Task Manager.
    $notes = New-List; $shots = New-List; $evidence = New-List
    if ((Get-ZfProcessIds).Count -eq 0) { Start-FromStartMenu $notes; [void] (Wait-Health 60) }
    $running = (Get-ZfProcessIds) -join ","
    $exeBefore = (Get-Item (Join-Path $script:AppDir "z-finance.exe")).LastWriteTime
    $finished = Start-UpdateSetup $notes $shots
    $a = $script:AppRunning
    [void] $evidence.Add("z-finance.exe running when Setup was started: $running")
    [void] $evidence.Add("Setup asked to close z-finance: $($a.asked) time(s); its words: $($a.text)")
    [void] $evidence.Add("after the tray 'Quit': z-finance ended: $($a.quit); left running: '$($a.left)'")
    [void] $evidence.Add("'unable to close all applications' seen: $script:CloseFailedSeen; wizard reached Finish: $finished")
    $result = "fail"
    if ($running -and $a.asked -eq 1 -and $a.quit -and -not $script:CloseFailedSeen -and $finished) { $result = "pass" }
    Add-Step 13 $result $evidence $notes $shots "13b"
    # No Stop-HungZfinance here: the z-finance that runs now is the fresh one "Launch z-finance" on
    # the last setup page started, not one that outlived "Quit".
    $after = New-List
    [void] $after.Add("update started while z-finance ran (row 13b), quit through the tray when Setup asked")
    [void] $after.Add("z-finance.exe after Finish (started by 'Launch z-finance'): $((Get-ZfProcessIds) -join ',')")
    Complete-Step13 $exeBefore $after (New-List) (New-List)
}

function Complete-Step13($ExeBefore, $Evidence, [System.Collections.ArrayList] $Notes, $Shots) {
    # The part of step 13 both ways share: one program folder, the booking survived, it starts.
    Start-Sleep -Seconds 5
    $dirs = @(Get-ChildItem (Join-Path $env:LOCALAPPDATA "Programs") -Directory | ForEach-Object { $_.Name })
    [void] $Evidence.Add("program folders below %LOCALAPPDATA%\Programs: $($dirs -join ', ')")
    Start-FromStartMenu $Notes
    $health = Wait-Health 60
    if ($null -ne $health) { $script:Port = $health.port }
    $stored = Test-Booking
    [void] (Open-InEdge "127.0.0.1:$script:Port/entry")
    [void] $Shots.Add((Save-Screenshot $ResultsDir 13 "booking-after-update"))
    [void] $Evidence.Add("z-finance.exe written before/after: $ExeBefore / $((Get-Item (Join-Path $script:AppDir 'z-finance.exe')).LastWriteTime)")
    [void] $Evidence.Add("booking in GET /api/transactions after the update: $stored")
    $one = $dirs.Count -eq 1 -and $dirs[0] -eq "z-finance"
    $result = "fail"; if ($one -and $stored -and $null -ne $health) { $result = "pass" }
    Add-Step 13 $result $Evidence $Notes $Shots
}

function Step14 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    [void] (Open-InEdge "127.0.0.1:$script:Port/settings")
    $box = Find-Web $script:Names.WebUpdate "CheckBox" 20
    if ($null -eq $box) { Add-Step 14 "fail" @() @("checkbox 'Look for new versions' not found") @(); return }
    if ((Get-ToggleState $box) -ne "On") { Invoke-Element $box $notes "Look for new versions"; Start-Sleep -Seconds 2 }
    $downloads = @(Get-ChildItem (Join-Path $env:USERPROFILE "Downloads") | ForEach-Object { $_.Name })
    $check = Find-Web $script:Names.WebCheckNow "Button" 10
    if ($null -ne $check) { Invoke-Element $check $notes "Check now"; Start-Sleep -Seconds 8 }
    [void] $shots.Add((Save-Screenshot $ResultsDir 14 "update-check"))
    $state = Get-ApiJson $script:Port "/api/update-check"
    $after = @(Get-ChildItem (Join-Path $env:USERPROFILE "Downloads") | ForEach-Object { $_.Name })
    [void] $evidence.Add("GET /api/update-check after 'Check now': $state")
    [void] $evidence.Add("files in Downloads before/after: $($downloads.Count) / $($after.Count)")
    # The expected outcomes on the page: a version named, or "could not reach GitHub".
    $answer = Find-WebContaining @("could not reach GitHub", "nicht erreichen", "and it is the newest one",
        "und das ist die neueste", "is available; you run", "ist verfügbar") 10
    $answerText = ""; if ($null -ne $answer) { $answerText = $answer.Current.Name }
    [void] $evidence.Add("answer on the page: $answerText")
    $result = "fail"; if ($null -ne $check -and $null -ne $answer -and $after.Count -eq $downloads.Count) { $result = "pass" }
    Add-Step 14 $result $evidence $notes $shots
}

function Find-RowButton($Entry, [string[]] $Names) {
    # Every row of the app list has its own "..." button; take the one level with the entry.
    $rowY = $Entry.Current.BoundingRectangle.Y
    foreach ($window in (Get-ProcessWindows @("SystemSettings", "ApplicationFrameHost"))) {
        foreach ($item in $window.FindAll($script:Scope::Descendants, $script:AnyCondition)) {
            if (-not ($Names -contains $item.Current.Name)) { continue }
            if ([Math]::Abs($item.Current.BoundingRectangle.Y - $rowY) -lt 40) { return $item }
        }
    }
    return $null
}

function Invoke-SettingsUninstall([System.Collections.ArrayList] $Notes, [System.Collections.ArrayList] $Shots) {
    # Settings > Apps > Installed apps > "..." next to z-finance > Uninstall > Uninstall.
    Start-Process "ms-settings:appsfeatures"
    Start-Sleep -Seconds 6
    $entry = Find-NamedPrefix @("SystemSettings", "ApplicationFrameHost") "z-finance" 20
    if ($null -eq $entry) { [void] $Notes.Add("z-finance not listed in Settings > Apps"); return $false }
    [void] $Shots.Add((Save-Screenshot $ResultsDir 15 "settings-apps"))
    $button = Find-RowButton $entry @("Weitere Optionen", "More options")
    if ($null -eq $button) { [void] $Notes.Add("'More options' of the z-finance row not found"); return $false }
    Invoke-Element $button $Notes "More options"
    $uninstall = Find-Named @("SystemSettings", "ApplicationFrameHost") @("Deinstallieren", "Uninstall") 10 "MenuItem"
    if ($null -eq $uninstall) { [void] $Notes.Add("menu entry 'Uninstall' not found"); return $false }
    [void] $Shots.Add((Save-Screenshot $ResultsDir 15 "settings-menu"))
    # The entry's UIA Invoke fails ("Unrecognized error") and a plain click does nothing: the
    # flyout wants the pointer to rest on the entry first, as a hand on the mouse does.
    $rect = $uninstall.Current.BoundingRectangle
    [ZfInput]::HoverClick([int] ($rect.X + $rect.Width / 2), [int] ($rect.Y + $rect.Height / 2))
    $confirm = Find-Named @("SystemSettings", "ApplicationFrameHost") @("Deinstallieren", "Uninstall") 10 "Button"
    if ($null -eq $confirm) {
        [void] $Shots.Add((Save-Screenshot $ResultsDir 15 "settings-no-confirm"))
        [void] $Notes.Add("the confirming 'Uninstall' button did not appear; " + (Get-UninstallDiagnosis))
        return $false
    }
    [void] $Shots.Add((Save-Screenshot $ResultsDir 15 "settings-confirm"))
    Click-Element $confirm $false
    return $true
}

function Find-UninstallerButton([string[]] $Names, [int] $TimeoutSeconds) {
    # The uninstaller's message boxes run in a temporary copy of unins000.exe; find them by title.
    $titles = @("z-finance Uninstall", "z-finance deinstallieren", "z-finance Deinstallation")
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $boxes = $script:Uia::RootElement.FindAll($script:Scope::Children, $script:AnyCondition) |
            Where-Object { $titles -contains $_.Current.Name }
        foreach ($box in $boxes) {
            $button = Find-NamedBelow $box $Names ""
            if ($null -ne $button) { return $button }
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Get-UninstallDiagnosis {
    $names = @(Get-Process | Where-Object { $_.ProcessName -match "^unins|^_iu|z-finance" } | ForEach-Object { "$($_.ProcessName)#$($_.Id)" })
    return "processes [$($names -join ', ')]; uninstaller windows [$((Get-SetupWindowTitles) -join ' / ')]"
}

function Confirm-Uninstaller([System.Collections.ArrayList] $Notes, [System.Collections.ArrayList] $Shots) {
    $yes = Find-UninstallerButton @("Ja", "Yes") 30
    if ($null -eq $yes) {
        [void] $Shots.Add((Save-Screenshot $ResultsDir 15 "no-uninstall-question"))
        [void] $Notes.Add("the uninstaller's question did not appear; " + (Get-UninstallDiagnosis))
        return $false
    }
    [void] $Shots.Add((Save-Screenshot $ResultsDir 15 "uninstall-question"))
    Invoke-Element $yes $Notes "Yes"
    $ok = Find-UninstallerButton @("OK") 60
    if ($null -ne $ok) {
        [void] $Shots.Add((Save-Screenshot $ResultsDir 15 "uninstall-done"))
        Invoke-Element $ok $Notes "OK"
    }
    return $true
}

function Step15 {
    $notes = New-List; $shots = New-List; $evidence = New-List
    $stopped = Stop-ByTray $notes
    [void] $evidence.Add("program quit through the tray first: $stopped")
    Stop-HungZfinance $evidence $notes
    $viaSettings = (Invoke-SettingsUninstall $notes $shots) -and (Confirm-Uninstaller $notes $shots)
    if (-not $viaSettings) {
        [void] $notes.Add("deviation: uninstalled with unins000.exe from the program folder, not through Settings")
        # Started through Explorer like a double-click, so its windows are shown.
        Start-Process explorer.exe -ArgumentList "`"$(Join-Path $script:AppDir 'unins000.exe')`""
        [void] (Confirm-Uninstaller $notes $shots)
    }
    Start-Sleep -Seconds 5
    $exeGone = -not (Test-Path (Join-Path $script:AppDir "z-finance.exe"))
    $linkGone = -not (Test-Path $script:StartMenuLink)
    $db = @(Get-ChildItem $script:DataDir -Filter "*.sqlite3" -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
    $backups = @(Get-ChildItem (Join-Path $script:DataDir "backups") -Directory -ErrorAction SilentlyContinue).Count
    Start-Process explorer.exe -ArgumentList "`"$script:DataDir`""
    Start-Sleep -Seconds 3
    [void] $shots.Add((Save-Screenshot $ResultsDir 15 "data-kept"))
    [void] $evidence.Add("uninstalled through Settings: $viaSettings; z-finance.exe gone: $exeGone; Start menu entry gone: $linkGone")
    [void] $evidence.Add("%LOCALAPPDATA%\z-finance still holds: $($db -join ', '), $backups backup folder(s)")
    $result = "fail"
    if ($exeGone -and $linkGone -and $db.Count -gt 0 -and $backups -gt 0) { $result = "pass" }
    Add-Step 15 $result $evidence $notes $shots
}
