# Windows: crea una tarea programada diaria (correr en PowerShell desde la carpeta del proyecto).
$dir = (Get-Location).Path
$accion = New-ScheduledTaskAction -Execute "$dir\.venv\Scripts\python.exe" `
  -Argument "-m gastos sync" -WorkingDirectory $dir
$disparador = New-ScheduledTaskTrigger -Daily -At 7:30
Register-ScheduledTask -TaskName "RegistroDeGastos" -Action $accion -Trigger $disparador `
  -Description "Descarga diaria de movimientos bancarios"
