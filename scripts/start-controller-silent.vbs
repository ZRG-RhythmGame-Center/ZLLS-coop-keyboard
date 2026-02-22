' 静默启动 Controller（无黑窗口）。可放入“启动”文件夹或由任务计划程序调用。
Set sh = CreateObject("WScript.Shell")
' 脚本所在目录的上一级为项目根
scriptDir = Replace(WScript.ScriptFullName, WScript.ScriptName, "")
projDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(scriptDir)
sh.CurrentDirectory = projDir
sh.Run "cmd /c uv run zlls-coop-keyboard-controller", 0, False
