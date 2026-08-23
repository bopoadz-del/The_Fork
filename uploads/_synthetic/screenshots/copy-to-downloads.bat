@echo off
setlocal
rem Copy this folder (all_png, infra_pack_ui_aug23, leftover_holiday_spark, README)
rem into %USERPROFILE%\Downloads\the_fork_screenshots and open Explorer there.
rem No Google Drive / G: paths — destination is the Windows Downloads folder.

set "SRC=%~dp0"
set "DEST=%USERPROFILE%\Downloads\the_fork_screenshots"

if not exist "%DEST%" mkdir "%DEST%"
xcopy /E /I /Y "%SRC%*" "%DEST%\"
if errorlevel 1 (
  echo Copy failed.
  pause
  exit /b 1
)

explorer "%DEST%"
endlocal
