# UI Automation, screenshot and evidence helpers for install_protocol.ps1.
#
# Runs inside Windows Sandbox only (Windows PowerShell 5.1). Dot-sourced, never run on its own.
# Every click goes through UI Automation by the visible name of the control; SendKeys and raw
# mouse input are the fallback, and each use of a fallback is written into the step's notes.

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class ZfInput {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, IntPtr e);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint f, IntPtr e);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hwnd);
    public static void Click(int x, int y, bool right) {
        SetCursorPos(x, y);
        uint down = right ? 0x0008u : 0x0002u;
        uint up = right ? 0x0010u : 0x0004u;
        mouse_event(down, 0, 0, 0, IntPtr.Zero);
        mouse_event(up, 0, 0, 0, IntPtr.Zero);
    }
    public static void HoverClick(int x, int y) {
        SetCursorPos(x, y);
        System.Threading.Thread.Sleep(500);
        mouse_event(0x0002u, 0, 0, 0, IntPtr.Zero);
        System.Threading.Thread.Sleep(120);
        mouse_event(0x0004u, 0, 0, 0, IntPtr.Zero);
    }
    public static void DoubleClick(int x, int y) {
        Click(x, y, false);
        System.Threading.Thread.Sleep(80);
        Click(x, y, false);
    }
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindWindow(string cls, string name);
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr hwnd, uint msg, IntPtr w, IntPtr l);
    [DllImport("user32.dll")] static extern int GetMenuItemCount(IntPtr menu);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern int GetMenuString(IntPtr menu, uint item, System.Text.StringBuilder text, int max, uint flags);
    public static string[] OpenPopupMenuItems() {
        // The labels of the open popup menu (class #32768), read with the Win32 menu API.
        IntPtr popup = FindWindow("#32768", null);
        if (popup == IntPtr.Zero) return new string[0];
        IntPtr menu = SendMessage(popup, 0x01E1, IntPtr.Zero, IntPtr.Zero);
        int count = menu == IntPtr.Zero ? 0 : GetMenuItemCount(menu);
        var items = new string[Math.Max(count, 0)];
        for (int i = 0; i < items.Length; i++) {
            var text = new System.Text.StringBuilder(256);
            GetMenuString(menu, (uint) i, text, 256, 0x400);
            items[i] = text.ToString().Replace("&", "");
        }
        return items;
    }
    public static void WinKey() {
        keybd_event(0x5B, 0, 0, IntPtr.Zero);
        keybd_event(0x5B, 0, 2, IntPtr.Zero);
    }
    public static void WinChord(byte vk) {
        keybd_event(0x5B, 0, 0, IntPtr.Zero);
        keybd_event(vk, 0, 0, IntPtr.Zero);
        keybd_event(vk, 0, 2, IntPtr.Zero);
        keybd_event(0x5B, 0, 2, IntPtr.Zero);
    }
}
"@

$script:Uia = [System.Windows.Automation.AutomationElement]
$script:Scope = [System.Windows.Automation.TreeScope]
$script:AnyCondition = [System.Windows.Automation.Condition]::TrueCondition

function ConvertTo-PlainName([string] $Name) {
    # Win32 buttons carry their mnemonic as "&Weiter >"; compare without it and without padding.
    if ($null -eq $Name) { return "" }
    return ($Name -replace "&", "").Trim()
}

function Get-ProcessWindows([string[]] $ProcessPrefixes) {
    # Top-level windows of every process whose name starts with one of the prefixes.
    $ids = @(Get-Process | Where-Object {
            $p = $_.ProcessName
            ($ProcessPrefixes | Where-Object { $p -like "$_*" }).Count -gt 0
        } | ForEach-Object { $_.Id })
    if ($ids.Count -eq 0) { return @() }
    $all = $script:Uia::RootElement.FindAll($script:Scope::Children, $script:AnyCondition)
    return @($all | Where-Object { $ids -contains $_.Current.ProcessId })
}

function Find-Named {
    # The first element below the given processes' windows whose plain name matches one of $Names
    # exactly; $null after $TimeoutSeconds.
    param([string[]] $ProcessPrefixes, [string[]] $Names, [int] $TimeoutSeconds = 20, [string] $ControlType = "")
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        foreach ($window in (Get-ProcessWindows $ProcessPrefixes)) {
            $found = Find-NamedBelow $window $Names $ControlType
            if ($null -ne $found) { return $found }
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Find-NamedBelow($Root, [string[]] $Names, [string] $ControlType = "") {
    if ($Names -contains (ConvertTo-PlainName $Root.Current.Name) -and $ControlType -eq "") { return $Root }
    try { $items = $Root.FindAll($script:Scope::Descendants, $script:AnyCondition) } catch { return $null }
    foreach ($item in $items) {
        try {
            $current = $item.Current
            if (-not ($Names -contains (ConvertTo-PlainName $current.Name))) { continue }
            if ($ControlType -ne "" -and $current.ControlType.ProgrammaticName -ne "ControlType.$ControlType") { continue }
            return $item
        } catch { continue }
    }
    return $null
}

function Invoke-Element($Element, [System.Collections.ArrayList] $Notes, [string] $Label) {
    # Invoke, toggle or select through UIA; a mouse click on the element's point is the fallback.
    try {
        foreach ($pattern in $Element.GetSupportedPatterns()) {
            $name = $pattern.ProgrammaticName
            if ($name -eq "InvokePatternIdentifiers.Pattern") { $Element.GetCurrentPattern($pattern).Invoke(); return }
            if ($name -eq "SelectionItemPatternIdentifiers.Pattern") { $Element.GetCurrentPattern($pattern).Select(); return }
            if ($name -eq "TogglePatternIdentifiers.Pattern") { $Element.GetCurrentPattern($pattern).Toggle(); return }
        }
        $reason = "no UIA pattern"
    } catch { $reason = "UIA pattern failed: $($_.Exception.Message)" }
    Click-Element $Element $false
    [void] $Notes.Add("'$Label' clicked with the mouse at its UIA position ($reason)")
}

function Click-Element($Element, [bool] $Right) {
    $rect = $Element.Current.BoundingRectangle
    $x = [int] ($rect.X + $rect.Width / 2)
    $y = [int] ($rect.Y + $rect.Height / 2)
    [ZfInput]::Click($x, $y, $Right)
}

function Get-ToggleState($Element) {
    try {
        $pattern = $Element.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
        return $pattern.Current.ToggleState.ToString()
    } catch { return "" }
}

function Send-Text([string] $Keys) {
    [System.Windows.Forms.SendKeys]::SendWait($Keys)
}

function Set-FieldText($Element, [string] $Text) {
    # Click into the field like a person, then select all and overwrite. The click also brings the
    # window to the front; without it, Windows keeps keystrokes from a background process away.
    Click-Element $Element $false
    Start-Sleep -Milliseconds 400
    Send-Text "^a"
    Send-Text $Text
    Start-Sleep -Milliseconds 300
}

function Save-Screenshot([string] $ResultsDir, [int] $Step, [string] $Name) {
    Start-Sleep -Milliseconds 800
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $file = "install-{0:D2}-{1}.png" -f $Step, $Name
    $bitmap.Save((Join-Path $ResultsDir $file), [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose(); $bitmap.Dispose()
    return $file
}

function Get-Health([int[]] $Ports = (8765..8775)) {
    # The first answer of /api/health on the launcher's port range, as {port, body}; $null if none.
    foreach ($port in $Ports) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$port/api/health"
            return [pscustomobject] @{ port = $port; status = [int] $response.StatusCode; body = $response.Content }
        } catch { continue }
    }
    return $null
}

function Wait-Health([int] $TimeoutSeconds = 60) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $health = Get-Health
        if ($null -ne $health) { return $health }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Get-LastLogLine([string] $DataDir) {
    $log = Join-Path $DataDir "runtime\z-finance.log"
    if (-not (Test-Path $log)) { return "" }
    return [string] (Get-Content $log -Tail 1)
}

function Get-ApiJson([int] $Port, [string] $Path) {
    # Read-only evidence: what the program stores, asked after the click, never instead of it.
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$Port$Path"
        return $response.Content
    } catch { return "" }
}
