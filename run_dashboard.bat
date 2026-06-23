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
git add data.json
git commit -m "sync"
git pull --rebase -X theirs
git push

echo.
echo ================================
echo  Klaar! Dashboard bijgewerkt.
echo  https://mordox89.github.io/garmin-data
echo ================================
pause