@echo off
title Build VRChat Photo Organizer EXE
cd /d "%~dp0"

echo Installing PyInstaller if needed...
python -m pip install pyinstaller

echo.
echo Building EXE...
python -m PyInstaller --onefile --windowed --name "VRChat Photo Organizer" "vrchat_photo_organizer_gui.py"

echo.
echo Done.
echo Your EXE should be in the "dist" folder.
echo.
pause
