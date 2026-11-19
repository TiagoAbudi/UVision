@echo off
title UStudio Vision
powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath 'C:\UStudioSmartHome\camera-ai\.venv\Scripts\pythonw.exe' -ArgumentList @('-m','camera_ai.web_dashboard','--open-browser') -WorkingDirectory 'C:\UStudioSmartHome\camera-ai' -WindowStyle Hidden"
