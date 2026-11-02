# Helpers for the three surfaces of the protocol: the installer, the tray icon and the browser.
#
# Runs inside Windows Sandbox only. Dot-sourced by install_protocol.ps1 after sandbox_uia.ps1.
# Button names are searched in German and English: the sandbox takes the host's language.

$script:SetupProcesses = @("z-finance-setup", "is-", "_iu", "unins")
$script:Names = @{
    MoreInfo     = @("Weitere Informationen", "More info")
    RunAnyway    = @("Trotzdem ausführen", "Run anyway")
    Ok           = @("OK")
    ForMeOnly    = @("Install for me only (recommended)", "Install for me only",
        "Nur für mich installieren (empfohlen)", "Nur für mich installieren")
    # Inno Setup's SetupAppRunningError (AppMutex) and its Restart Manager failure, en and de.
    AppRunning   = @("is currently running", "zur Zeit ausgeführt")
    CloseFailed  = @("unable to automatically close", "nicht automatisch schließen")
    StoreNo      = @("No", "Nein")
    StoreTitle   = @("Search for app in the Store?", "Im Store nach einer App suchen?")
    Accept       = @("Ich akzeptiere die Vereinbarung", "I accept the agreement")
    Next         = @("Weiter", "Next", "Weiter >", "Next >")
    Install      = @("Installieren", "Install")
    Finish       = @("Fertigstellen", "Finish")
    HiddenIcons  = @("Ausgeblendete Symbole anzeigen", "Show hidden icons")
    TrayOpen     = @("z-finance öffnen", "Open z-finance")
    TrayBackup   = @("Jetzt sichern", "Back up now")
    TrayFolder   = @("Datenordner öffnen", "Open data folder")
    TrayQuit     = @("Beenden", "Quit")
    AddressBar   = @("Address and search bar", "Adress- und Suchleiste")
    WebContinue  = @("Weiter", "Continue")
    WebFinish    = @("Fertig", "Finish")
    WebName      = @("Bezeichnung", "Name")
    WebBalance   = @("Saldo heute", "Balance today")
    WebStartPage = @("Zur Startseite", "To the start page")
    WebAmount    = @("Betrag (€)", "Amount (€)")
    WebText      = @("Buchungstext", "Text")
    WebCategory  = @("Kategorie", "Category")
    WebSave      = @("Speichern", "Save")
    WebUpdate    = @("Nach neuen Versionen suchen", "Look for new versions")
    WebCheckNow  = @("Jetzt prüfen", "Check now")
}

function Invoke-SetupWizard([string] $ResultsDir, [int] $Step, [System.Collections.ArrayList] $Notes) {
    # Click through the Inno Setup pages by button name until "Finish"; one picture per page.
    # Returns the list of screenshot files.
    # Inno Setup draws its own controls: UIA sees every button, radio and box as a "Pane", so the
    # search here goes by name only.
    $shots = New-Object System.Collections.ArrayList
    if (-not (Wait-SetupWindow 60)) { [void] $Notes.Add("no setup window appeared"); return , $shots }
    $accepted = $false
    for ($page = 1; $page -le 20; $page++) {
        Start-Sleep -Seconds 2
        $mode = Find-Named $script:SetupProcesses $script:Names.ForMeOnly 1 ""
        if ($null -ne $mode) {
            [void] $shots.Add((Save-Screenshot $ResultsDir $Step "setup-install-mode"))
            [void] $Notes.Add("the setup asked 'Select Setup Install Mode' (all users / me only); clicked 'Install for me only'")
            Invoke-Element $mode $Notes "Install for me only"; continue
        }
        # Checked before the generic "OK": Setup's "close z-finance" question has an OK button too.
        $asking = Find-SetupWindowWithText $script:Names.AppRunning
        if ($null -ne $asking) { Resolve-AppRunningQuestion $asking $ResultsDir $Step $Notes $shots; continue }
        if ($null -ne (Find-SetupWindowWithText $script:Names.CloseFailed)) {
            $script:CloseFailedSeen = $true
            [void] $shots.Add((Save-Screenshot $ResultsDir $Step "setup-could-not-close"))
            [void] $Notes.Add("the setup said it was unable to close all applications; not clicked through")
            break
        }
        if (Invoke-SetupFinish $ResultsDir $Step $Notes $shots) { return , $shots }
        if (-not $accepted) {
            $accept = Find-Named $script:SetupProcesses $script:Names.Accept 1 ""
            if ($null -ne $accept) { Invoke-Element $accept $Notes "accept"; $accepted = $true; Start-Sleep -Seconds 1 }
        }
        $target = $null
        foreach ($names in @($script:Names.Ok, $script:Names.Install, $script:Names.Next)) {
            if ($null -eq $target) { $target = Find-Named $script:SetupProcesses $names 1 "" }
        }
        if ($null -eq $target) { continue }
        [void] $shots.Add((Save-Screenshot $ResultsDir $Step ("setup-page{0:D2}" -f $page)))
        Invoke-Element $target $Notes (ConvertTo-PlainName $target.Current.Name)
    }
    if ($shots.Count -eq 0) { [void] $Notes.Add("no setup window appeared") }
    else { [void] $Notes.Add("the wizard did not reach its last page") }
    [void] $shots.Add((Save-Screenshot $ResultsDir $Step "setup-stalled"))
    [void] $Notes.Add("setup controls: " + (Get-ControlDump))
    return , $shots
}

function Find-SetupWindowWithText([string[]] $Parts) {
    # The setup window whose own name or any text inside it contains one of $Parts; $null if none.
    foreach ($window in (Get-ProcessWindows $script:SetupProcesses)) {
        try { $items = @($window) + @($window.FindAll($script:Scope::Descendants, $script:AnyCondition)) } catch { continue }
        foreach ($item in $items) {
            $name = ""
            try { $name = $item.Current.Name } catch { continue }
            foreach ($part in $Parts) { if ($name -and $name.Contains($part)) { return $window } }
        }
    }
    return $null
}

function Get-WindowTexts($Window) {
    $texts = @($Window.Current.Name)
    try { $texts += @($Window.FindAll($script:Scope::Descendants, $script:AnyCondition) | ForEach-Object { $_.Current.Name }) } catch { }
    return (@($texts | Where-Object { $_ }) -join " / ")
}

function Invoke-SetupFinish([string] $ResultsDir, [int] $Step, [System.Collections.ArrayList] $Notes, $Shots) {
    # The last page. The "Launch z-finance" box stays ticked, as a lay user leaves it, so Finish
    # starts the program (step 5).
    $finish = Find-Named $script:SetupProcesses $script:Names.Finish 1 ""
    if ($null -eq $finish) { return $false }
    [void] $Shots.Add((Save-Screenshot $ResultsDir $Step "setup-finish"))
    Invoke-Element $finish $Notes "Finish"
    Start-Sleep -Seconds 5
    if (Get-Process z-finance -ErrorAction SilentlyContinue) { [void] $Notes.Add("z-finance started after Finish") }
    return $true
}

function Start-SetupLikeDoubleClick {
    # Explorer shows Downloads with the file selected, and a mouse double-click opens it.
    # Explorer runs the file, so SmartScreen and the setup get normal, visible windows. (Started
    # from a hidden helper process, both stayed invisible; a direct ShellExecute blocks this script.)
    Start-Process explorer.exe -ArgumentList "/select,`"$script:LocalSetup`""
    $name = [IO.Path]::GetFileNameWithoutExtension($script:LocalSetup)
    $item = Find-Named @("explorer") @($name) 20 "ListItem"
    if ($null -eq $item) { return $false }
    $rect = $item.Current.BoundingRectangle
    [ZfInput]::DoubleClick([int] ($rect.X + 40), [int] ($rect.Y + $rect.Height / 2))
    Start-Sleep -Seconds 3
    $shell = New-Object -ComObject Shell.Application
    $shell.Windows() | Where-Object { try { $_.Document.Folder.Self.Path -eq (Split-Path $script:LocalSetup) } catch { $false } } |
        ForEach-Object { $_.Quit() }
    return $true
}

function Close-StoreQuestion([System.Collections.ArrayList] $Notes) {
    # "Search for app in the Store?" is what Windows Sandbox shows instead of SmartScreen.
    $windows = $script:Uia::RootElement.FindAll($script:Scope::Children, $script:AnyCondition) |
        Where-Object { $script:Names.StoreTitle -contains $_.Current.Name }
    foreach ($window in $windows) {
        $no = Find-NamedBelow $window $script:Names.StoreNo "Button"
        if ($null -ne $no) { Invoke-Element $no $Notes "No (Store question)" }
    }
}

function Get-ControlDump([string[]] $Prefixes = $script:SetupProcesses) {
    # Name and control type of every named control in the setup's windows, for a stalled wizard.
    $parts = @()
    foreach ($window in (Get-ProcessWindows $Prefixes)) {
        foreach ($item in $window.FindAll($script:Scope::Descendants, $script:AnyCondition)) {
            $name = $item.Current.Name
            if ($name) { $parts += "$name<$($item.Current.ControlType.ProgrammaticName -replace 'ControlType.', '')>" }
        }
    }
    return ($parts | Select-Object -First 40) -join "; "
}

function Get-SetupWindowTitles {
    return @(Get-ProcessWindows $script:SetupProcesses | Where-Object { -not $_.Current.IsOffscreen } |
            ForEach-Object { $_.Current.Name })
}

function Wait-SetupWindow([int] $TimeoutSeconds) {
    # True once the setup shows any window: the language question or, without it, the wizard.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ((Get-SetupWindowTitles).Count -gt 0) { return $true }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Get-LaunchDiagnosis {
    # Why neither SmartScreen nor the setup showed: what runs, whether the network answers, and
    # what the SmartScreen policy says. Evidence for a "not run", never a verdict on its own.
    $names = @(Get-Process | Where-Object { $_.ProcessName -match "smartscreen|setup|^is-|z-finance" } |
            ForEach-Object { "$($_.ProcessName)#$($_.Id)" })
    $net = "no"
    try { $net = [string] (Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 "https://github.com/").StatusCode } catch { $net = "error: $($_.Exception.Message)" }
    $policy = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer" -ErrorAction SilentlyContinue).SmartScreenEnabled
    $titles = (Get-SetupWindowTitles) -join " / "
    return "processes [$($names -join ', ')]; setup windows [$titles]; github.com: $net; SmartScreenEnabled=$policy"
}

function Start-SetupWithoutMark($Evidence, [System.Collections.ArrayList] $Notes) {
    # Separates "SmartScreen hangs in the sandbox" from "the setup does not start": the same file,
    # without its Mark of the Web, started the same way. Recorded as a deviation for step 3.
    Close-StoreQuestion $Notes
    Get-Process | Where-Object { $_.ProcessName -like "z-finance-setup*" } | Stop-Process -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $script:LocalSetup -Stream Zone.Identifier -ErrorAction SilentlyContinue
    [void] (Start-SetupLikeDoubleClick)
    $started = Wait-SetupWindow 60
    [void] $Evidence.Add("same file without Mark of the Web: setup window appeared: $started")
    [void] $Evidence.Add("diagnosis after the second start: " + (Get-LaunchDiagnosis))
    if ($started) { [void] $Notes.Add("deviation: the setup for step 3 was started without its Mark of the Web") }
}

function Open-TrayMenu([System.Collections.ArrayList] $Notes) {
    # Right-click the z-finance icon, in the overflow area when Windows put it there.
    $icon = Find-Named @("explorer") @("z-finance 1.0.0") 3 ""
    if ($null -eq $icon) {
        $chevron = Find-Named @("explorer") $script:Names.HiddenIcons 10 ""
        if ($null -ne $chevron) { Invoke-Element $chevron $Notes "hidden icons" }
        Start-Sleep -Seconds 1
        $icon = Find-NamedPrefix @("explorer") "z-finance" 10
    }
    if ($null -eq $icon) { return $false }
    Click-Element $icon $true
    Start-Sleep -Seconds 1
    return $true
}

function Find-NamedPrefix([string[]] $ProcessPrefixes, [string] $Prefix, [int] $TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        foreach ($window in (Get-ProcessWindows $ProcessPrefixes)) {
            $items = $window.FindAll($script:Scope::Descendants, $script:AnyCondition)
            foreach ($item in $items) {
                try { if ((ConvertTo-PlainName $item.Current.Name).StartsWith($Prefix)) { return $item } } catch { continue }
            }
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Get-TrayMenuItems([int] $TimeoutSeconds = 5) {
    # UIA does not see pystray's popup from this process; the Win32 menu API does.
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $items = @([ZfInput]::OpenPopupMenuItems())
        if ($items.Count -gt 0) { return $items }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    return @()
}

function Invoke-TrayEntry([string[]] $Names, [System.Collections.ArrayList] $Notes) {
    # Open the menu, find the entry's position by its label, then arrow keys and Enter: the
    # menu holds the keyboard while it is open.
    if (-not (Open-TrayMenu $Notes)) { return $false }
    $items = @(Get-TrayMenuItems)
    for ($i = 0; $i -lt $items.Count; $i++) {
        if ($Names -contains $items[$i]) {
            Send-Text ("{DOWN " + ($i + 1) + "}")
            Start-Sleep -Milliseconds 300
            Send-Text "{ENTER}"
            [void] $Notes.Add("tray entry '$($items[$i])' chosen with the arrow keys (the menu is not visible to UIA)")
            return $true
        }
    }
    Send-Text "{ESC}"
    return $false
}

function Focus-Edge {
    $window = @(Get-ProcessWindows @("msedge") | Where-Object { $_.Current.Name -ne "" }) | Select-Object -First 1
    if ($null -eq $window) { return $null }
    [void] [ZfInput]::SetForegroundWindow([IntPtr] $window.Current.NativeWindowHandle)
    Start-Sleep -Milliseconds 500
    return $window
}

function Open-InEdge([string] $Url) {
    # Typed into the address bar like a person would; the browser is the one z-finance opened.
    if ($null -eq (Focus-Edge)) { return $false }
    $bar = Find-Web $script:Names.AddressBar "Edit" 10
    if ($null -eq $bar) { return $false }
    Set-FieldText $bar $Url
    Send-Text "{ENTER}"
    Start-Sleep -Seconds 4
    return $true
}

function Find-WebContaining([string[]] $Parts, [int] $TimeoutSeconds) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        foreach ($window in (Get-ProcessWindows @("msedge"))) {
            foreach ($item in $window.FindAll($script:Scope::Descendants, $script:AnyCondition)) {
                $name = ""
                try { $name = $item.Current.Name } catch { continue }
                foreach ($part in $Parts) { if ($name -and $name.Contains($part)) { return $item } }
            }
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Find-Web([string[]] $Names, [string] $ControlType = "", [int] $TimeoutSeconds = 20) {
    return Find-Named @("msedge") $Names $TimeoutSeconds $ControlType
}

function Start-FromStartMenu([System.Collections.ArrayList] $Notes) {
    # Windows key, type the name, Enter: the way a person starts a program from the Start menu.
    [ZfInput]::WinKey()
    Start-Sleep -Seconds 2
    Send-Text "z-finance"
    Start-Sleep -Seconds 3
    Send-Text "{ENTER}"
}

function Set-EdgePolicies {
    # Edge's own first-run pages would cover the page z-finance opens. This prepares the sandbox's
    # browser; it changes nothing about z-finance and is listed as a deviation in the protocol.
    $key = "HKLM:\SOFTWARE\Policies\Microsoft\Edge"
    try {
        New-Item -Path $key -Force | Out-Null
        Set-ItemProperty -Path $key -Name HideFirstRunExperience -Value 1 -Type DWord
        Set-ItemProperty -Path $key -Name DefaultBrowserSettingEnabled -Value 0 -Type DWord
        Set-ItemProperty -Path $key -Name RestoreOnStartup -Value 5 -Type DWord
        return $true
    } catch { return $false }
}
