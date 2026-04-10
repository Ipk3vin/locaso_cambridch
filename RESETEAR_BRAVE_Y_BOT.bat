@echo off
echo ==================================================
echo   RESET DE CONEXION BRAVE - BOT CAMBRIDGE
echo ==================================================
echo.
echo 1. Cerrando procesos de Brave existentes...
taskkill /F /IM brave.exe /T >nul 2>&1
timeout /t 2 >nul
echo.
echo 2. Iniciando Brave en MODO DEPURACION...
start "" "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222 --user-data-dir="%cd%\BraveProfileBot"
echo.
echo 3. ¡LISTO!
echo    - Espera a que cargue Brave.
echo    - Ve a la pestaña de Cambridge.
echo    - Ejecuta 'python script.py' en la otra ventana.
echo.
pause
