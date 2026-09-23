$ErrorActionPreference='Stop'
[Console]::InputEncoding=[Text.Encoding]::UTF8
[Console]::OutputEncoding=[Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$data=[Console]::In.ReadToEnd() | ConvertFrom-Json
if($data.action -eq 'status'){
 $s=New-Object System.Speech.Synthesis.SpeechSynthesizer
 try{ @{recognizers=@([System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | ForEach-Object {$_.Culture.Name});voices=@($s.GetInstalledVoices() | ForEach-Object {$_.VoiceInfo.Name})} | ConvertTo-Json -Compress } finally {$s.Dispose()}
}elseif($data.action -eq 'tts'){
 $s=New-Object System.Speech.Synthesis.SpeechSynthesizer
 try{if($data.voice){$s.SelectVoice($data.voice)};$s.SetOutputToWaveFile($data.output);$s.Speak($data.text);$s.SetOutputToNull();'{"ok":true}'}finally{$s.Dispose()}
}elseif($data.action -eq 'asr'){
 $info=[System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | Where-Object {$_.Culture.Name -eq $data.language} | Select-Object -First 1
 if(!$info){throw 'Recognition language unavailable'}
 $r=New-Object System.Speech.Recognition.SpeechRecognitionEngine($info)
 try{
  $r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
  $r.InitialSilenceTimeout=[TimeSpan]::FromSeconds(60)
  $r.EndSilenceTimeout=[TimeSpan]::FromMilliseconds(500)
  $r.SetInputToWaveFile($data.input)
  $parts=New-Object 'System.Collections.Generic.List[string]';$confidence=1.0
  while($true){
   try{$result=$r.Recognize()}catch{if($_.Exception.InnerException -is [InvalidOperationException] -and $r.AudioState -eq [System.Speech.Recognition.AudioState]::Stopped){break};throw}
   if(!$result){if($r.AudioState -eq [System.Speech.Recognition.AudioState]::Stopped){break};continue};$parts.Add($result.Text);$confidence=[Math]::Min($confidence,$result.Confidence)
  }
  @{text=($parts -join ' ');confidence=$confidence} | ConvertTo-Json -Compress
 }finally{$r.Dispose()}
}
