' Start bailing LLM gateway without console window (double-click to run)
Set fso = CreateObject("Scripting.FileSystemObject")
base = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(base)
Set ws = CreateObject("Wscript.Shell")
ws.Run """" & base & "\bailing-gateway.exe"" --config " & root & "\config --port 8766", 0, False
