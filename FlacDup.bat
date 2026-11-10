@echo off
rem Startet FlacDup mit der Conda-Umgebung "flacdup" (ohne Konsolenfenster)
cd /d "%~dp0"
start "" "%USERPROFILE%\anaconda3\envs\flacdup\pythonw.exe" main.py
