@echo off
rem Build the ONLINE WAR module (online.c) with the Visual Studio 2022 Build Tools x86 compiler and paste
rem it into ..\patch_online.py (python patch_online.py embed online.dll). No C run-time: /Zl, /GS-, /Oi-;
rem one .text section (rdata/data/bss merged) in a relocatable DLL that the tool rebases.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat" >nul 2>&1
if errorlevel 1 echo vcvars32.bat not found & exit /b 1
cd /d "%~dp0"
cl /nologo /c /O1 /Oi- /GS- /Gy- /Zl /W3 /Gs999999 /Fo:online.obj online.c || exit /b 1
link /nologo /NODEFAULTLIB /NOENTRY /DLL /SUBSYSTEM:WINDOWS,4.0 /MERGE:.rdata=.text /MERGE:.data=.text /MERGE:.bss=.text /OUT:online.dll online.obj || exit /b 1
if "%1"=="--embed" python ..\patch_online.py embed online.dll || exit /b 1
del online.obj online.exp online.lib 2>nul
endlocal
