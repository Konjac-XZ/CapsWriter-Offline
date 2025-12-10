; 为原来的脚本提供了另一种显示方式(使用BeautifulToolTip库),并且提供修改配置的功能,在这个`hint_while_recording.ini`文件修改。
; 使用BeautifulToolTip库显示的好处是不会改变焦点,从而退出全屏幕模式(需要在`config.py` 的 `hint_while_recording_at_cursor_position = False`配合使用)。另外比较漂亮。坏处是增加了30MB记忆体的占用。

; hint_while_recording:
; Author:[H1DDENADM1N](https://github.com/H1DDENADM1N/CapsWriter-Offline)
; Contributor: [JoanthanWu](https://github.com/JoanthanWu/CapsWriter-Offline)
; 第三方库`BeautifulToolTip`:
; Author: [telppa](https://github.com/telppa/BeautifulToolTip)
; Contributor: [liuyi91](https://github.com/liuyi91/ahkv2lib-)
; Contributor: [thqby](https://github.com/thqby)

#Requires AutoHotkey v2.0
#SingleInstance Force
#Include BTTv2.ahk
#Include GetCaretPosEx.ahk
InstallKeybdHook
InstallMouseHook
TraySetIcon(A_ScriptDir "\assets\hint_while_recording.ico", 1)
CoordMode("ToolTip", "Screen")

IniFile := A_ScriptDir "\hint_while_recording.ini"
if !FileExist(IniFile) {
    DefaultIni()
}
ReadIni()

global keyPressed := False
global is_microphone_in_use := False
global is_short_duration := False
global offline_translate_needed := false
global online_translate_needed := false
global hold_mode := False
global bttRemoveLoopIndex := 0
global lastX := 0
global lastY := 0
global 调用次数 := 0
global 调用次数A := 0
global 调用次数B := 0
global 调用次数C := 0

OnMessage 0x5555, MsgMonitor
Persistent

OwnStyle1 := { TextColorLinearGradientStart: cnTxtClolorA        ; ARGB
    , TextColorLinearGradientEnd: cnTxtClolorB         ; ARGB
    , TextColorLinearGradientAngle: 0                 ; Mode=8 Angle 0(L to R) 90(U to D) 180(R to L) 270(D to U)
    , TextColorLinearGradientMode: 2                  ; Mode=4 Angle 0(L to R) 90(D to U), Range 1-8.
    , BackgroundColor: 0x00ffffff
    , FontSize: cnTxtFontSize
    , FontRender: 4
    , FontStyle: "Bold" }
OwnStyle2 := { TextColorLinearGradientStart: enTxtClolorA        ; ARGB
    , TextColorLinearGradientEnd: enTxtClolorB         ; ARGB
    , TextColorLinearGradientAngle: 0                 ; Mode=8 Angle 0(L to R) 90(U to D) 180(R to L) 270(D to U)
    , TextColorLinearGradientMode: 2                  ; Mode=4 Angle 0(L to R) 90(D to U), Range 1-8.
    , BackgroundColor: 0x00ffffff
    , FontSize: enTxtFontSize
    , FontRender: 4
    , FontStyle: "Bold" }
OwnStyle3 := { TextColorLinearGradientStart: cnTxtClolorB        ; ARGB
    , TextColorLinearGradientEnd: cnTxtClolorA          ; ARGB
    , TextColorLinearGradientAngle: 0                 ; Mode=8 Angle 0(L to R) 90(U to D) 180(R to L) 270(D to U)
    , TextColorLinearGradientMode: 2                  ; Mode=4 Angle 0(L to R) 90(D to U), Range 1-8.
    , BackgroundColor: 0x00ffffff
    , FontSize: cnTxtFontSize
    , FontRender: 4
    , FontStyle: "Bold" }
OwnStyle4 := { TextColorLinearGradientStart: enTxtClolorB        ; ARGB
    , TextColorLinearGradientEnd: enTxtClolorA          ; ARGB
    , TextColorLinearGradientAngle: 0                 ; Mode=8 Angle 0(L to R) 90(U to D) 180(R to L) 270(D to U)
    , TextColorLinearGradientMode: 2                  ; Mode=4 Angle 0(L to R) 90(D to U), Range 1-8.
    , BackgroundColor: 0x00ffffff
    , FontSize: enTxtFontSize
    , FontRender: 4
    , FontStyle: "Bold" }

; SetTimer 监测, 200
; 监测(*) {
;     ToolTip "is_microphone_in_use= " is_microphone_in_use "`nis_short_duration= " is_short_duration "`n使用的次数: " 调用次数 "`noffline_translate_needed= " offline_translate_needed "`nonline_translate_needed= " online_translate_needed "`nhold_mode= " hold_mode "`n调用次数A= " 调用次数A "`n调用次数B= " 调用次数B "`n调用次数C= " 调用次数C "`nbttRemoveLoopIndex= " bttRemoveLoopIndex, 0, 0, 10
; }

; ============================  主控  ============================

MsgMonitor(wParam, lParam, msg, hwnd) {
    global is_microphone_in_use, is_short_duration, offline_translate_needed, online_translate_needed, hold_mode, keyPressed, 调用次数, bttRemoveLoopIndex

    is_microphone_in_use := (wParam & 1) != 0
    is_short_duration := (wParam & 2) != 0
    offline_translate_needed := (wParam & 4) != 0
    online_translate_needed := (wParam & 8) != 0
    hold_mode := (wParam & 16) != 0

    ; ToolTip "is_microphone_in_use= " is_microphone_in_use "`nis_short_duration= " is_short_duration "`n使用的次数: " 调用次数 "`noffline_translate_needed= " offline_translate_needed "`nonline_translate_needed= " online_translate_needed "`nhold_mode= " hold_mode "`n调用次数A= " 调用次数A "`n调用次数B= " 调用次数B "`n调用次数C= " 调用次数C "`nbttRemoveLoopIndex= " bttRemoveLoopIndex, 0, 1080, 9
    ; 调用次数 += 1

    if is_short_duration
        keyPressed := false

    if !is_microphone_in_use {
        bttRemoveLoopIndex := 0
        SetTimer BttRemoveLoop, 50
        return
    }
    else if (offline_translate_needed OR online_translate_needed) AND (is_microphone_in_use) {
        EnglishVoice()
        return
    }
    else If (is_microphone_in_use) {
        ChineseVoice()
        return
    }
}


ChineseVoice(*) {
    global keyPressed, is_short_duration, is_microphone_in_use, hold_mode
    SetTimer BttRemoveLoop, 0
    ; 在doNotShowHintList中的程序将不会显示“语音输入中”的提示
    if (hold_mode AND !keyPressed AND !offline_translate_needed AND !online_translate_needed) {
        keyPressed := true

        exe_name := ""
        try {
            exe_name := ProcessGetName(WinGetPID("A"))
            DllCall("SetThreadDpiAwarenessContext", "ptr", -2, "ptr")
            ; ToolTip(exe_name)
        }
        if (InStr(doNotShowHintList, ":" exe_name ":")) {
            return
        }
        ShowIt(&x, &y, &w, &h, "cnTxt")

    } else If (!hold_mode AND !keyPressed AND !offline_translate_needed AND !online_translate_needed) {
        keyPressed := true

        exe_name := ""
        try {
            exe_name := ProcessGetName(WinGetPID("A"))
            DllCall("SetThreadDpiAwarenessContext", "ptr", -2, "ptr")
            ; ToolTip(exe_name)
        }
        if (InStr(doNotShowHintList, ":" exe_name ":")) {
            return
        }
        ShowIt(&x, &y, &w, &h, "cnTxt")
    }
}


EnglishVoice(*) {
    global keyPressed, is_short_duration, is_microphone_in_use, hold_mode
    SetTimer BttRemoveLoop, 0
    ; 在doNotShowHintList中的程序将不会显示“语音输入中”的提示
    if (hold_mode AND !keyPressed) {
        keyPressed := true

        exe_name := ""
        try {
            exe_name := ProcessGetName(WinGetPID("A"))
            DllCall("SetThreadDpiAwarenessContext", "ptr", -2, "ptr")
            ; ToolTip(exe_name)
        }
        if (InStr(doNotShowHintList, ":" exe_name ":")) {
            return
        }
        ShowIt(&x, &y, &w, &h, "enTxt")

    } else If (!hold_mode AND !keyPressed) {
        keyPressed := true

        exe_name := ""
        try {
            exe_name := ProcessGetName(WinGetPID("A"))
            DllCall("SetThreadDpiAwarenessContext", "ptr", -2, "ptr")
            ; ToolTip(exe_name)
        }
        if (InStr(doNotShowHintList, ":" exe_name ":")) {
            return
        }
        ShowIt(&x, &y, &w, &h, "enTxt")
    }
}


; ============================  显示和移除提示  ============================


ShowIt(&x, &y, &w, &h, Txt) {
    global 调用次数A
    if hwnd := GetCaretPosEx(&x, &y, &w, &h) {
        ; 能够获取到文本光标时，提示信息在输入光标位置，且x坐标向右偏移5
        x := x + 5
        TipShow(Txt, x, y)
        ; 调用次数A += 1
        return
    }
    else {
        ; 获取不到文本光标时，提示信息在鼠标光标的位置
        global followingTxt := Txt
        SetTimer FollowingMouseLoop, 50
        return
    }
}

FollowingMouseLoop(*) {
    global keyPressed, lastX, lastY, 调用次数B, 调用次数C
    keyPressed := true
    CoordMode "Mouse", "Screen"  ; 确保MouseGetPos使用的是屏幕坐标
    MouseGetPos(&newX, &newY)  ; 获取鼠标的当前位置
    if (newX != lastX || newY != lastY) {  ; 如果鼠标位置发生变化
        lastX := newX
        lastY := newY
        TipShow(followingTxt, lastX, lastY)
        ; 调用次数B += 1
    }
    else If (!is_microphone_in_use) {
        SetTimer FollowingMouseLoop, 0
        SetTimer BttRemoveLoop, 50
    }
    ; 调用次数C += 1
}

TipShow(Txt, x, y) {
    if enableBTT
        if Txt == "cnTxt" {
            if is_short_duration
                btt(cnTxtB, x, y - 3, 20, OwnStyle3)
            else
                btt(cnTxt, x, y - 3, 20, OwnStyle1)
        }
        else {
            if is_short_duration
                btt(enTxtB, x, y - 3, 20, OwnStyle4)
            else
                btt(enTxt, x, y - 3, 20, OwnStyle2)
        }
    else
        if Txt == "cnTxt" {
            if is_short_duration
                ToolTip(cnTxtB, x, y)
            else
                ToolTip(cnTxt, x, y)
        }
        else {
            if is_short_duration
                ToolTip(enTxtB, x, y)
            else
                ToolTip(enTxt, x, y)
        }
}

BttRemoveLoop(*) {
    global keyPressed, bttRemoveLoopIndex
    if keyPressed {
        if enableBTT
            btt(, , , 20)
        else
            ToolTip()
        bttRemoveLoopIndex := 0
        keyPressed := false
        ; 如果还出现提示没有被移除的情况, 把下面 SetTimer 这一句删掉/注释
        SetTimer BttRemoveLoop, 0
        ;ToolTip "BttRemoveLoop A`n" bttRemoveLoopIndex, 200, 200, 8
        return
    }
    else if (bttRemoveLoopIndex <= 60)
        bttRemoveLoopIndex := bttRemoveLoopIndex + 1
    else if (bttRemoveLoopIndex > 60) {
        ; keyPressed := False
        SetTimer BttRemoveLoop, 0
        ;ToolTip "BttRemoveLoop B`n" bttRemoveLoopIndex, 300, 300, 9
        bttRemoveLoopIndex := 0
        return
    }
}


; ============================  配置信息  ============================

DefaultIni() {
    IniWrite("1", IniFile, "BeautifulToolTip", "enableBTT")
    IniWrite("✦语音输入中‧‧‧", IniFile, "ShowText", "cnTxt")
    IniWrite("✦语音输入中⇄", IniFile, "ShowText", "cnTxtB")
    IniWrite("✦VoiceTrans‧‧‧", IniFile, "ShowText", "enTxt")
    IniWrite("✦VoiceTrans⇄", IniFile, "ShowText", "enTxtB")
    IniWrite("0xFFCC7A00", IniFile, "Txt", "cnTxtClolorA")
    IniWrite("0xFFFFDF80", IniFile, "Txt", "cnTxtClolorB")
    IniWrite("16", IniFile, "Txt", "cnTxtFontSize")
    IniWrite("0xFF1A1AFF", IniFile, "Txt", "enTxtClolorA")
    IniWrite("0xFF6666FF", IniFile, "Txt", "enTxtClolorB")
    IniWrite("16", IniFile, "Txt", "enTxtFontSize")
    IniWrite("在hintAtCursorPositionList中的程序将不会把“语音输入中”的提示显示在文本光标位置，而是显示在鼠标光标的位置", IniFile, "List", "Comment1")
    IniWrite(":StartMenuExperienceHost.exe:wetype_update.exe:AnLink.exe:wps.exe:HBuilderX.exe:ShareX.exe:clipdiary-portable.exe:explorer.exe:", IniFile, "List", "hintAtCursorPositionList")
    IniWrite("在doNotShowHintList中的程序将不会显示“语音输入中”的提示", IniFile, "List", "Comment2")
    IniWrite(":PotPlayer.exe:PotPlayer64.exe:PotPlayerMini.exe:PotPlayerMini64.exe:", IniFile, "List", "doNotShowHintList")
}

ReadIni() {
    global enableBTT := IniRead(IniFile, "BeautifulToolTip", "enableBTT")
    global cnTxt := IniRead(IniFile, "ShowText", "cnTxt")
    global cnTxtB := IniRead(IniFile, "ShowText", "cnTxtB")
    global enTxt := IniRead(IniFile, "ShowText", "enTxt")
    global enTxtB := IniRead(IniFile, "ShowText", "enTxtB")
    global cnTxtClolorA := IniRead(IniFile, "Txt", "cnTxtClolorA")
    global cnTxtClolorB := IniRead(IniFile, "Txt", "cnTxtClolorB")
    global cnTxtFontSize := IniRead(IniFile, "Txt", "cnTxtFontSize")
    global enTxtClolorA := IniRead(IniFile, "Txt", "enTxtClolorA")
    global enTxtClolorB := IniRead(IniFile, "Txt", "enTxtClolorB")
    global enTxtFontSize := IniRead(IniFile, "Txt", "enTxtFontSize")
    global doNotShowHintList := IniRead(IniFile, "List", "doNotShowHintList")
}