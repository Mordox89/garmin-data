@echo off
echo ================================
echo  Marathon Dashboard Sync
echo ================================
cd /d "%~dp0"

echo.
echo [1/3] Garmin data ophalen...
python garmin_fetch.py
if errorlevel 1 (
    echo FOUT: garmin_fetch.py mislukt
    pause
    exit /b 1
)

echo.
echo [2/3] Pushen naar GitHub...
git pull --rebase -X theirs
git add -A
git diff --staged --quiet || git commit -m "Manual sync"
git push

echo.
echo ================================
echo  Klaar! Dashboard bijgewerkt.
echo  AI feedback wordt automatisch
echo  gegenereerd via GitHub Actions.
echo ================================
pause