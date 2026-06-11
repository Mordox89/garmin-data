@echo off
echo ================================
echo  Marathon Dashboard Sync
echo ================================
cd /d "%~dp0"

echo.
echo [1/2] Garmin data ophalen...
python garmin_fetch.py
if errorlevel 1 (
    echo FOUT: garmin_fetch.py mislukt
    pause
    exit /b 1
)

echo.
echo [2/2] Pushen naar GitHub...
git add -A
git diff --staged --quiet || git commit -m "Manual sync: %date% %time%"
git push

echo.
echo ================================
echo  Klaar! GitHub Actions genereert
echo  de AI feedback automatisch.
echo ================================
pause
