@echo off
echo Reset de Brave (Usando tu Perfil de Siempre)...
taskkill /F /IM brave.exe /T >nul 2>&1
timeout /t 2 >nul
start "" "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222
echo Brave abierto. Abre Cambridge y luego corre el bot.
pause
