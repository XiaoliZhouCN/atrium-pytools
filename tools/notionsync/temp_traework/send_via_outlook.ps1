param(
  [string]$Subject,
  [string]$HtmlPath,
  [string]$Recipients
)

$ErrorActionPreference = "Stop"
$outlook = $null

for ($i = 0; $i -lt 5; $i++) {
  try {
    $outlook = [System.Runtime.InteropServices.Marshal]::GetActiveObject("Outlook.Application")
    if ($outlook -ne $null) { break }
  } catch {}

  try {
    $outlook = New-Object -ComObject Outlook.Application
    if ($outlook -ne $null) { break }
  } catch {
    if ($i -eq 4) { throw }
    Start-Sleep -Seconds 2
  }
}

if ($outlook -eq $null) {
  throw "Unable to connect to Outlook.Application."
}

$mail = $outlook.CreateItem(0)
$mail.Subject = $Subject
$mail.To = $Recipients
$mail.HTMLBody = Get-Content -Raw -Encoding UTF8 $HtmlPath
$mail.Send()

Write-Output "SENT_OK"
