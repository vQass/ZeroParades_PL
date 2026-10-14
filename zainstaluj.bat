@echo off
rem Instaluje polskie tłumaczenie Zero Parades (języki „Polski” i „Polski + EN”).
rem Użycie: zainstaluj.bat "G:\SteamLibrary\steamapps\common\Zero Parades"  (albo dwuklik – zapyta o folder)
chcp 65001 >nul
cd /d "%~dp0"
set "GAME=%~1"
if "%GAME%"=="" set /p "GAME=Podaj folder gry (zawiera ZeroParades_Data): "
if defined GAME set "GAME=%GAME:"=%"
if not exist "%GAME%\ZeroParades_Data" (
  echo Nie znaleziono "%GAME%\ZeroParades_Data". Sprawdz sciezke.
  goto end
)
python -m pip install -q -r requirements.txt || goto end
rem paczka dla graczy: translation\pl.json; warsztat: out\pl_merged.json (budowany z plików roboczych)
set "TEXTS=translation\pl.json"
if not exist "%TEXTS%" set "TEXTS=out\pl_merged.json"
if not exist "%TEXTS%" python tools\build_translation.py || goto end
rem „Polski + EN” powstaje tutaj: angielski oryginał jest czytany z plików gry (--bilingual)
python tools\add_language.py --game "%GAME%" --texts "%TEXTS%" --bilingual --apply
:end
rem po dwukliku okno nie znika, zanim da się przeczytać wynik
if "%~1"=="" pause
