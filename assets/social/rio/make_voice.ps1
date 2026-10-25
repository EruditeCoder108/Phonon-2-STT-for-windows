$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Speech
$rioNarrator=New-Object System.Speech.Synthesis.SpeechSynthesizer
$rioNarrator.SelectVoice('Microsoft Zira Desktop')
$rioNarrator.Rate=1
$rioNarrator.Volume=75
$rioWave=Join-Path $PSScriptRoot 'voice-demo.wav'
$rioFormat=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(24000,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
Register-ObjectEvent -InputObject $rioNarrator -EventName SpeakProgress -SourceIdentifier RioVoiceWords | Out-Null
try {
  $rioNarrator.SetOutputToWaveFile($rioWave,$rioFormat)
  $rioNarrator.Speak("Good ideas shouldn't have to wait for a keyboard.")
  $rioNarrator.SetOutputToNull()
  $rioWords=@(Get-Event -SourceIdentifier RioVoiceWords -ErrorAction SilentlyContinue | ForEach-Object { @{word=$_.SourceEventArgs.Text;at=$_.SourceEventArgs.AudioPosition.TotalSeconds} })
  ConvertTo-Json -InputObject $rioWords -Depth 3 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'voice-words.json') -Encoding utf8
  Write-Output "Synthesized local narration and $($rioWords.Count) word markers."
} finally {
  Unregister-Event -SourceIdentifier RioVoiceWords -ErrorAction SilentlyContinue
  Get-Event -SourceIdentifier RioVoiceWords -ErrorAction SilentlyContinue | Remove-Event
  $rioNarrator.Dispose()
}
