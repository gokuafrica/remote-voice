' Remote Voice launcher (hidden window, detached)
' Invoked by "Remote Voice.bat". Checks the one-time install, then starts the
' Electron app with no visible console and returns immediately.
Option Explicit

Dim fso, shell, root, appDir, electronExe
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

root = fso.GetParentFolderName(WScript.ScriptFullName)
appDir = root & "\app"
electronExe = appDir & "\node_modules\electron\dist\electron.exe"

If Not fso.FileExists(electronExe) Then
  MsgBox "Remote Voice is not installed yet." & vbCrLf & vbCrLf & _
         "Run the one-time install:" & vbCrLf & _
         "  1. Open a terminal in:" & vbCrLf & "     " & appDir & vbCrLf & _
         "  2. Run:  npm install", _
         vbCritical, "Remote Voice"
  WScript.Quit 1
End If

shell.CurrentDirectory = appDir
' 0 = hidden window, False = do not wait (fully detached)
shell.Run """" & electronExe & """ .", 0, False
