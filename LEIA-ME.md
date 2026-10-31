# Dock para Windows — instalador e atualizações

A pessoa baixa **um arquivo** (`Dock-Setup.exe`), clica duas vezes e pronto: a dock é instalada, abre
sozinha e passa a iniciar junto com o Windows. Quando você publica uma versão nova, as docks já
instaladas se atualizam sozinhas.

## O que tem nesta pasta

| Arquivo | Para quê |
|---|---|
| `dock.py` | O programa. No topo ficam `VERSAO` e `GITHUB_REPO`. |
| `atualizador.py` | Procura e baixa versões novas no GitHub. |
| `installer/Dock.iss` | Receita do instalador (Inno Setup). |
| `.github/workflows/release.yml` | O GitHub monta o `.exe` e o instalador sozinho. |
| `build.ps1` | Alternativa: montar o instalador no seu próprio PC. |
| `icon.ico` | Ícone do programa e do instalador. |

## 1. Configurar (uma vez só)

1. Crie uma conta em https://github.com e um repositório **público** chamado `Dock`.
   (Público é necessário: as docks das outras pessoas precisam conseguir ler as versões publicadas.)
2. No `dock.py`, troque a linha `GITHUB_REPO = ""` pelo seu usuário e repositório:
   ```python
   GITHUB_REPO = "seu-usuario/Dock"
   ```
3. Suba esta pasta para o repositório (no PowerShell, dentro da pasta):
   ```powershell
   git init
   git add .
   git commit -m "Dock 1.0.0"
   git branch -M main
   git remote add origin https://github.com/seu-usuario/Dock.git
   git push -u origin main
   ```

## 2. Publicar a primeira versão

```powershell
git tag v1.0.0
git push origin v1.0.0
```

Em uns 5 minutos aparece em **GitHub > seu repositório > Releases** o arquivo `Dock-Setup.exe`.
Esse é o arquivo que você manda para as pessoas (o link do release serve para compartilhar).
Se algo falhar, a aba **Actions** do repositório mostra o erro.

## 3. Publicar uma atualização (todo mundo recebe)

1. Mexa no código como quiser.
2. No `dock.py`, aumente o número: `VERSAO = "1.0.1"`.
3. Envie e crie a tag **com o mesmo número**:
   ```powershell
   git add .
   git commit -m "Dock 1.0.1: o que mudou"
   git push
   git tag v1.0.1
   git push origin v1.0.1
   ```

As docks conferem o GitHub ao abrir (depois de ~45 s) e a cada 6 horas. Ao achar uma versão nova, baixam o
instalador, conferem o hash SHA-256 e instalam em silêncio: a dock some por alguns segundos e volta
atualizada. Cada pessoa pode desligar isso em **Preferências > Atualizações**, e o botão
**Verificar agora** força a checagem.

> A tag precisa bater com o `VERSAO` (v1.0.1 ⇄ "1.0.1"), senão o GitHub recusa de propósito.
> Só recebem atualizações as docks cuja versão instalada já tinha o `GITHUB_REPO` preenchido.

## Sem GitHub (só instalador)

Instale o Python e o Inno Setup 6 e rode `.\build.ps1`. O instalador sai em
`installer\Output\Dock-Setup.exe`. Dá para distribuir, mas sem as atualizações automáticas.

## Onde ficam as coisas depois de instalar

- Programa: `%LOCALAPPDATA%\Programs\Dock`
- Apps fixados, preferências e logs: `%APPDATA%\Dock` (botão **Abrir pasta da dock** nas Preferências)
- Para levar sua dock atual para a versão instalada, copie `dock_config.json` e `dock_prefs.json` da
  pasta antiga para `%APPDATA%\Dock`.
- Desinstalar: Configurações do Windows > Aplicativos > Dock. A barra de tarefas do Windows volta ao normal.

## Avisos honestos

- **Aviso do Windows (SmartScreen).** O `.exe` não é assinado digitalmente, então o Windows pode mostrar
  "O Windows protegeu o computador". É só clicar em *Mais informações > Executar assim mesmo*. Alguns
  antivírus também desconfiam de programas feitos com PyInstaller. Para acabar com isso é preciso um
  certificado de assinatura de código (pago).
- **Apps iniciais.** Na primeira abertura, a dock mostra só os programas que existem naquele PC
  (Explorador de Arquivos, Edge, Chrome, Firefox...). Cada pessoa monta o resto (botão direito > Adicionar).
- **Segurança das atualizações.** Quem controlar sua conta do GitHub controla o que todo mundo recebe:
  ative a verificação em duas etapas nela.
