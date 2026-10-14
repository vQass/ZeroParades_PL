@echo off
rem Przywraca oryginalne pliki gry z kopii w backup\ (albo: Steam -> Sprawdz spojnosc plikow gry).
chcp 65001 >nul
cd /d "%~dp0"
set "GAME=%~1"
if "%GAME%"=="" set /p "GAME=Podaj folder gry (zawiera ZeroParades_Data): "
if defined GAME set "GAME=%GAME:"=%"
python tools\install.py --game "%GAME%" --restore
rem po dwukliku okno nie znika, zanim da się przeczytać wynik
if "%~1"=="" pause
