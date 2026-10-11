@echo off
rem Trae2api-cn tray launcher (no window).
rem A .bat double-click always flashes a cmd console briefly; it hands off to
rem the VBS (hidden) and exits immediately, so no window stays open.
rem For troubleshooting with live logs, use the debug .bat instead.
for %%F in ("%~dp0*.vbs") do (
  wscript //nologo "%%~fF"
  goto :end
)
:end
