@echo off
start "" "C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe" --remote-debugging-port=9222 --user-data-dir="%cd%\BraveProfileBot"
echo Brave abierto en modo depuracion.
pause
