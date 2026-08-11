Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
exePath = scriptDir & "\dist\TimeLogOCR.exe"
If fso.FileExists(exePath) Then
  shell.Run """" & exePath & """", 0, False
Else
  shell.Run """" & scriptDir & "\run_dev.bat""", 0, False
End If
