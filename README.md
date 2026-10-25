# YouFA 2.2

Seu organizador de faixas de áudio — by Fabiano Abrão.

## Download para Windows

**[Baixar o instalador YouFA 2.2.0](https://github.com/fabianoabrao-oss/youfa-desktop/releases/download/v2.2.0/YouFA-Setup-2.2.0.exe)**

O instalador é para Windows 64 bits. Como ainda não possui assinatura digital
comercial, o Windows pode exibir um aviso do SmartScreen. O checksum SHA-256
está disponível na [página da versão](https://github.com/fabianoabrao-oss/youfa-desktop/releases/tag/v2.2.0).

Aplicativo local para Windows que extrai áudio de URLs do YouTube para uma pasta escolhida pelo usuário.

Recursos:

- MP3, M4A, FLAC, WAV e Opus;
- vídeos individuais e playlists;
- separação por capítulos/timestamps;
- separação experimental por pausas de silêncio;
- identificação gratuita por metadados do YouTube e capítulos;
- escolha entre manter o arquivo inteiro ou separar por mudanças acústicas;
- na separação bem-sucedida, mantém somente as faixas e remove o arquivo integral temporário;
- se a separação falhar, preserva o arquivo inteiro e mostra um acesso à pasta de destino;
- consulta opcional ao AudD, desativada por padrão e executada somente quando marcada;
- token AudD protegido pelo DPAPI do Windows;
- revisão antes de renomear e gravar tags;
- FFmpeg e yt-dlp incorporados ao executável;
- progresso, log e cancelamento.

Use somente conteúdo próprio, autorizado ou cujo download seja permitido.

O AudD oferece uma franquia gratuita e depois cobra conforme o uso. A conta e
os preços atuais ficam em https://dashboard.audd.io/ e https://www.audd.io/.
O token nunca é incluído no executável ou no instalador.

## Executar pelo código-fonte

Requisitos no Windows:

- Python 3.12 ou posterior no `PATH`;
- Node.js no `PATH`;
- PowerShell;
- Inno Setup apenas para gerar o instalador.

Crie o ambiente e instale as dependências:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

## Gerar o executável

```powershell
.\build.ps1
```

O script obtém o FFmpeg pelo pacote `imageio-ffmpeg`, localiza o Node.js no
`PATH` e gera `dist\YouFA.exe`. Os binários e artefatos de build não são
versionados.

Para gerar o instalador, abra `installer.iss` no Inno Setup depois do build.

## Segurança e privacidade

- O token AudD é opcional e armazenado localmente com DPAPI do Windows.
- Nenhum token é incluído no código, no executável ou no repositório.
- Downloads e reconhecimento devem ser usados somente com conteúdo autorizado.
